"""Opt-in tests over reviewed upstream source; never download or access live services.

LOOPRD_INTEGRATION_SOURCES points to a directory containing atrium/ and social/
complete descriptors and their includes. Tests use disposable databases/secrets.
"""

import copy
import json
import os
import secrets
from pathlib import Path

import httpx
import pytest
import yaml

from looprd.app import create_app
from looprd.config import Settings
from looprd.discovery_contract import digest
from looprd.discovery_outbox import DiscoveryOutbox
from looprd.discovery_store import DiscoveryStore
from looprd.discovery_transport import DeliveryFailure, DiscoveryTransport


UPSTREAM = os.getenv("LOOPRD_INTEGRATION_SOURCES")
pytestmark = pytest.mark.skipif(not UPSTREAM, reason="Reviewed upstream integration sources not configured")
ROOT = Path(__file__).resolve().parents[1]
RECEIVER = "https://catalog.example.com/looprd/atrium/v1/registry/discovery/v2"
ADMIN = {"Authorization": "Bearer isolated-review-admin"}


def configure(tmp_path, entries):
    secret_dir = tmp_path / "secrets"
    secret_dir.mkdir(exist_ok=True)
    (secret_dir / "admin-token").write_text("isolated-review-admin")
    sources = tmp_path / "sources.yaml"
    sources.write_text(yaml.safe_dump({"apiVersion": "looprd/v1", "components": entries}))
    return Settings(sources=str(sources), data=tmp_path / "data", secrets=secret_dir,
                    allow_python=True, bootstrap_admin=False, poll_seconds=3600, drain_seconds=0)


def descriptor(component, repo):
    return {"id": component, "specification": str(Path(UPSTREAM).resolve() / repo / "looprd.yaml")}


async def issue(client, scopes, service=None):
    payload = {"label": "disposable integration identity", "scopes": scopes}
    if service:
        payload["service"] = service
    response = await client.post("/_looprd/components/atrium/keys", headers=ADMIN, json=payload)
    assert response.status_code == 201, response.text
    return response.json()["apiKey"]


@pytest.mark.asyncio
async def test_actual_atrium_receiver_with_durable_sender_restart_and_multiple_deployments(tmp_path):
    settings = configure(tmp_path, [descriptor("atrium", "atrium")])
    (tmp_path / "sender").mkdir()
    outbox = DiscoveryOutbox(DiscoveryStore(tmp_path / "sender"))
    body = json.loads((ROOT / "contracts/atrium-discovery-v2/upsert-git.json").read_text())
    for key in ("operationId", "sequence", "fingerprint"):
        body.pop(key)
    credentials = {}
    first_packet = None
    pending_packet = None
    catalog_headers = None

    async def deliver(app, credential, packet, lose_response=False):
        base = httpx.ASGITransport(app=app)

        async def wire(request):
            assert request.url.query == b""
            assert request.headers["authorization"] == "Bearer " + credential
            if request.method == "POST":
                assert credential.encode() not in request.content
            response = await base.handle_async_request(request)
            if lose_response and request.method == "POST":
                await response.aread()
                await response.aclose()
                raise httpx.ReadError("test response lost after commit")
            return response

        transport = DiscoveryTransport(httpx.AsyncClient(transport=httpx.MockTransport(wire)))
        try:
            return await transport.send(RECEIVER, credential, packet)
        finally:
            await transport.close()

    for restarting in (False, True):
        app = create_app(settings)
        async with app.router.lifespan_context(app):
            assert "atrium" in app.state.runtime.active, app.state.runtime.errors
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://catalog.example.com") as client:
                if not restarting:
                    operator = {"Authorization": "Bearer " + await issue(client, ["apps:approve"])}
                    catalog_headers = {"Authorization": "Bearer " + await issue(client, ["apps:read"])}
                    for deployment, caller in (("node-test", "sender-one"), ("node-second", "sender-two")):
                        credentials[deployment] = await issue(client, ["apps:discover"], caller)
                        response = await client.post(RECEIVER + "/manage", headers=operator, json={
                            "operation": "register", "payload": {"deploymentId": deployment,
                            "callerSubject": caller, "publisherId": "sample-publisher",
                            "apiOrigin": "https://api.example.com", "launchOrigins": {}}})
                        assert response.status_code == 200, response.text
                    for kind in ("git", "local", "https"):
                        body["source"]["kind"] = kind
                        body["source"]["revision"] = "c" * 40 if kind == "git" else None
                        outbox.ensure(body, RECEIVER)
                        packet = outbox.due("node-test", RECEIVER)[0]["envelope"]
                        ack = await deliver(app, credentials["node-test"], packet)
                        assert ack["status"] == "reviewed"
                        outbox.finish(packet, acknowledgement=ack)
                        first_packet = first_packet or copy.deepcopy(packet)
                    second = {**body, "deploymentId": "node-second"}
                    outbox.ensure(second, RECEIVER)
                    packet = outbox.due("node-second", RECEIVER)[0]["envelope"]
                    await deliver(app, credentials["node-second"], packet)
                    with pytest.raises(DeliveryFailure) as failure:
                        await deliver(app, credentials["node-test"], packet)
                    assert failure.value.reason == "receiver_binding_mismatch"
                    with pytest.raises(DeliveryFailure) as failure:
                        await deliver(app, "invalid-disposable-proof", packet)
                    assert failure.value.reason == "receiver_authentication"
                    body["sidecar"]["game"]["title"] = "Updated sample"
                    outbox.ensure(body, RECEIVER)
                    pending_packet = outbox.due("node-test", RECEIVER)[0]["envelope"]
                    with pytest.raises(DeliveryFailure):
                        await deliver(app, credentials["node-test"], pending_packet, lose_response=True)
                else:
                    # Both receiver and sender reconnect with their persisted databases.
                    outbox = DiscoveryOutbox(DiscoveryStore(tmp_path / "sender"))
                    assert outbox.due("node-test", RECEIVER)[0]["envelope"] == pending_packet
                    ack = await deliver(app, credentials["node-test"], pending_packet)
                    assert ack["fingerprint"] == pending_packet["fingerprint"]
                    outbox.finish(pending_packet, acknowledgement=ack)
                    withdrawn = {**body, "operation": "withdraw", "runtime": None,
                                 "sidecar": None, "reason": "component_removed"}
                    outbox.ensure(withdrawn, RECEIVER)
                    packet = outbox.due("node-test", RECEIVER)[0]["envelope"]
                    assert (await deliver(app, credentials["node-test"], packet))["status"] == "withdrawn"
                    # Old replay is acknowledged but must not revive the withdrawn instance.
                    assert (await deliver(app, credentials["node-test"], first_packet))["status"] == "reviewed"
                    stale = {**first_packet, "operationId": secrets.token_hex(16)}
                    assert stale["fingerprint"] == digest({k: v for k, v in stale.items()
                        if k not in ("operationId", "sequence", "fingerprint")})
                    response = await client.post(RECEIVER + "/observations",
                        headers={"Authorization": "Bearer " + credentials["node-test"]}, json=stale)
                    assert response.status_code == 409
                    catalog = await client.get(RECEIVER + "/catalog", headers=catalog_headers)
                    assert catalog.status_code == 200, catalog.text
                    games = catalog.json()["games"]
                    assert len(games) == 1, games
                    instances = games[0]["instances"]
                    # Catalog projects active instances, retaining tombstones in SQL.
                    assert len(instances) == 1
                    assert instances[0]["deploymentId"] == "node-second"


@pytest.mark.asyncio
async def test_actual_social_verifier_and_declared_service_call_keep_machine_proof_server_side(tmp_path, monkeypatch):
    caller = "integration-caller"
    secret_ref = "review-caller-proof"
    monkeypatch.setenv("LOOPRD_SOCIAL_SERVICE_SECRETS_JSON", json.dumps({caller: secret_ref}))
    monkeypatch.setenv("LOOPRD_TAVI_GAME_CALLERS_JSON", json.dumps({caller: ["sample.game"]}))
    monkeypatch.setenv("LOOPRD_TAVI_SOCIAL_ISSUER", "isolated-social-issuer")
    caller_spec = {"apiVersion": "looprd/v1", "component": caller, "release": "review-1",
        "api": {"version": "v1"}, "auth": {"mode": "public"},
        "dependencies": [{"component": "looprd-social", "contract": "tavi-game-grant-redeem",
                          "version": 1, "credentialSecret": secret_ref, "optional": True}],
        "handlers": [{"path": "/probe", "method": "POST", "kind": "python",
            "source": "async def handle(ctx): return await ctx.call('looprd-social', 'tavi-game-grant-redeem', ctx.body)"}]}
    path = tmp_path / "caller.yaml"
    path.write_text(yaml.safe_dump(caller_spec))
    settings = configure(tmp_path, [descriptor("looprd-social", "social"),
                                   {"id": caller, "specification": str(path)}])
    proof = secrets.token_urlsafe(48)
    (settings.secrets / secret_ref).write_text(proof)
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        assert not app.state.runtime.errors, app.state.runtime.errors
        target = app.state.runtime.active["looprd-social"]
        principal = await target.authenticate("Bearer " + proof)
        assert principal.kind == "service" and principal.subject == caller
        assert principal.permits(["social:service"])
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://api.example.com") as client:
            response = await client.post("/looprd/" + caller + "/v1/probe",
                json={"grant": "x" * 48, "gameId": "sample.game", "matchId": "match_review_001"})
            assert response.status_code == 200, response.text
            # Nonexistent user proof fails in Social, after valid machine authentication.
            assert response.json() == {"accepted": False, "reason": "not_available"}
            services = await client.get("/looprd/" + caller + "/v1/_services")
            assert services.status_code == 200
            assert services.json()["services"][0]["available"]
            assert proof not in response.text + services.text
            assert "credentialSecret" not in services.text
            (settings.secrets / secret_ref).unlink()
            response = await client.post("/looprd/" + caller + "/v1/probe",
                json={"grant": "x" * 48, "gameId": "sample.game", "matchId": "match_review_001"})
            assert response.status_code == 503

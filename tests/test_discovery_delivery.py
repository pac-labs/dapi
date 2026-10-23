"""DESIGN.adoc / Automatic Atrium discovery delivery: durable control-plane reconciliation."""

from pathlib import Path

import httpx
import pytest
import yaml

from looprd.discovery_source import SourceSnapshot
from looprd.discovery_transport import DiscoveryTransport
from looprd.runtime import Runtime
from test_discovery import sidecar, settle
from discovery_receiver_fixture import ReceiverFixture


async def prepare(settings, monkeypatch, receiver):
    settings.discovery_enabled = settings.discovery_delivery_enabled = True
    settings.deployment_id = "node-test"
    settings.public_url = "https://api.example.com"
    settings.discovery_receiver_url = "https://catalog.example.com/discovery/v2"
    settings.discovery_credential_secret = "discovery-token"
    (settings.secrets / "discovery-token").write_text("receiver-token")
    runtime = Runtime(settings)
    delivery = runtime.discovery.delivery
    monkeypatch.setattr(delivery, "start", lambda: None)
    await delivery.transport.close()
    delivery.transport = DiscoveryTransport(httpx.AsyncClient(transport=httpx.MockTransport(receiver), follow_redirects=False))
    return runtime, delivery


def due(delivery):
    with delivery.observer.store.connect() as db:
        db.execute("UPDATE discovery_delivery_v2 SET due=0")


async def observe(runtime):
    await runtime.reconcile()
    await settle(runtime)


async def test_valid_sidecar_idempotent_updates_location_and_provenance(setup_project, monkeypatch):
    settings, _, _ = setup_project
    path = Path(settings.sources).parent / "atriumd.yaml"
    path.write_text(yaml.safe_dump(sidecar()))
    receiver = ReceiverFixture()
    runtime, delivery = await prepare(settings, monkeypatch, receiver)
    try:
        await observe(runtime); await delivery.tick()
        first = receiver.posts[0]
        assert first["runtime"]["apiBaseUrl"] == "https://api.example.com/looprd/demo/v1"
        assert first["source"]["kind"] == "local" and first["source"]["revision"] is None
        assert str(path.parent) not in str(first) and "receiver-token" not in str(first)
        assert delivery.outbox.summaries("node-test")[0]["state"] == "acknowledged"
        await observe(runtime); await delivery.tick()
        assert len(receiver.posts) == 1
        changed = sidecar(); changed["game"]["title"] = "New sidecar only"
        path.write_text(yaml.safe_dump(changed))
        await observe(runtime); await delivery.tick()
        assert receiver.posts[-1]["sequence"] == 2
        assert receiver.posts[-1]["operationId"] != first["operationId"]
        settings.public_url = "https://api2.example.com"
        await delivery.tick()
        assert receiver.posts[-1]["runtime"]["apiBaseUrl"].startswith(settings.public_url)
        assert receiver.posts[-1]["sequence"] == 3
    finally:
        await runtime.close()


async def test_lost_ack_restart_replays_same_operation_and_accepts_reviewed(setup_project, monkeypatch):
    settings, _, _ = setup_project
    (Path(settings.sources).parent / "atriumd.yaml").write_text(yaml.safe_dump(sidecar()))
    receiver = ReceiverFixture(); receiver.lose_ack = True
    runtime, delivery = await prepare(settings, monkeypatch, receiver)
    await observe(runtime); await delivery.tick()
    first = receiver.posts[0]
    assert delivery.outbox.summaries("node-test")[0]["state"] == "retrying"
    await runtime.close()
    # Whole sources repository is unavailable: restart must still retry committed intent.
    Path(settings.sources).unlink()
    runtime, delivery = await prepare(settings, monkeypatch, receiver)
    receiver.reviewed = True
    try:
        await runtime.restore()
        assert not await runtime.reconcile()
        due(delivery); await delivery.tick()
        assert receiver.posts[-1] == first
        assert len(receiver.operations) == 1
        assert delivery.outbox.summaries("node-test")[0]["state"] == "acknowledged"
    finally:
        await runtime.close()


@pytest.mark.parametrize("failure", ["auth", "network", "legacy", "binding", "ack"])
async def test_failures_never_block_activation_and_recover(setup_project, monkeypatch, failure):
    settings, _, _ = setup_project
    (Path(settings.sources).parent / "atriumd.yaml").write_text(yaml.safe_dump(sidecar()))
    receiver = ReceiverFixture()
    runtime, delivery = await prepare(settings, monkeypatch, receiver)
    try:
        if failure == "auth": (settings.secrets / "discovery-token").write_text("wrong")
        if failure == "network": receiver.fail_status = 503
        if failure == "legacy": receiver.legacy = True
        if failure == "binding": receiver.wrong_binding = True
        if failure == "ack": receiver.bad_ack = True
        await observe(runtime); await delivery.tick()
        assert "demo" in runtime.active and not runtime.errors
        row = delivery.outbox.summaries("node-test")[0]
        assert row["state"] == "retrying" and row["nextAttemptAt"] > 0
        if failure in ("legacy", "binding", "auth"): assert not receiver.posts
        (settings.secrets / "discovery-token").write_text("receiver-token")
        receiver.fail_status = None; receiver.legacy = receiver.wrong_binding = receiver.bad_ack = False
        due(delivery); await delivery.tick()
        assert delivery.outbox.summaries("node-test")[0]["state"] == "acknowledged"
    finally:
        await runtime.close()


async def test_unavailability_invalid_and_withdrawal_are_distinct(setup_project, monkeypatch):
    settings, _, write_sources = setup_project
    path = Path(settings.sources).parent / "atriumd.yaml"
    path.write_text(yaml.safe_dump(sidecar()))
    receiver = ReceiverFixture()
    runtime, delivery = await prepare(settings, monkeypatch, receiver)
    try:
        await observe(runtime); await delivery.tick()
        path.write_text("unknown: invalid")
        await observe(runtime); await delivery.tick()
        assert len(receiver.posts) == 1
        root = path.parent / "looprd.yaml"
        old = root.read_text(); root.unlink()
        await observe(runtime); await delivery.tick()
        assert len(receiver.posts) == 1
        root.write_text(old); path.unlink()
        await observe(runtime); await delivery.tick()
        assert receiver.posts[-1]["operation"] == "withdraw"
        assert receiver.posts[-1]["reason"] == "sidecar_removed"
        path.write_text("unknown: invalid")
        await observe(runtime); await delivery.tick()
        assert len(receiver.posts) == 2  # invalid reappearance cannot resurrect a withdrawn game.
        path.write_text(yaml.safe_dump(sidecar()))
        await observe(runtime); await delivery.tick()
        write_sources([])
        await observe(runtime); await delivery.tick()
        assert receiver.posts[-1]["reason"] == "component_removed"
        assert len(receiver.operations) == 4
    finally:
        await runtime.close()


def test_provenance_and_stable_git_source_identity():
    git = SourceSnapshot("https://api.github.com/repos/org/game/contents/api/looprd.yaml?ref=" + "a"*40, "a"*40, "b"*64)
    other = SourceSnapshot(git.location.replace("a"*40,"c"*40),"c"*40,"b"*64)
    assert git.identity == other.identity and git.provenance()["revision"] == "a"*40
    for location, kind in [("https://metadata.example.com/looprd.yaml", "https"), ("/private/looprd.yaml", "local")]:
        source = SourceSnapshot(location, "content", "b"*64).provenance()
        assert source["kind"] == kind and source["revision"] is None


@pytest.mark.parametrize("kind", ["git", "https"])
async def test_remote_source_delivery_uses_real_provenance(setup_project,spec_data,monkeypatch,kind):
    settings, _, write_sources = setup_project
    if kind == "git":
        write_sources([{"id":"demo","repository":"https://github.com/example/game","specification":"looprd.yaml"}])
    else:
        write_sources([{"id":"demo","specification":"https://metadata.example.com/looprd.yaml"}])
        settings.discovery_https_origins=("https://metadata.example.com",)
    receiver=ReceiverFixture()
    runtime,delivery=await prepare(settings,monkeypatch,receiver)
    def respond(request):
        if "/commits/" in request.url.path: return httpx.Response(200,text="c"*40)
        return httpx.Response(200,text=yaml.safe_dump(sidecar() if request.url.path.endswith("atriumd.yaml") else spec_data))
    await runtime.reader.client.aclose()
    runtime.reader.client=httpx.AsyncClient(transport=httpx.MockTransport(respond))
    try:
        await observe(runtime); await delivery.tick()
        source=receiver.posts[0]["source"]
        assert source["kind"]==kind
        assert source["revision"]==("c"*40 if kind=="git" else None)
    finally: await runtime.close()


async def test_lifespan_starts_and_stops_delivery_and_exposes_protected_status(setup_project,monkeypatch):
    import asyncio
    from starlette.testclient import TestClient
    from looprd.app import create_app
    from looprd.discovery_delivery import DiscoveryDelivery
    from test_http import ADMIN
    settings, _, _=setup_project
    (Path(settings.sources).parent/"atriumd.yaml").write_text(yaml.safe_dump(sidecar()))
    runtime,delivery=await prepare(settings,monkeypatch,ReceiverFixture())
    monkeypatch.setattr(delivery,"start",lambda:DiscoveryDelivery.start(delivery))
    async def acknowledged():
        for _ in range(100):
            rows=delivery.outbox.summaries("node-test")
            if rows and rows[0]["state"]=="acknowledged": return True
            await asyncio.sleep(.02)
        return False
    with TestClient(create_app(settings,runtime)) as client:
        assert client.portal.call(acknowledged)
        assert client.get("/_looprd/discovery").status_code==401
        response=client.get("/_looprd/discovery",headers=ADMIN)
        assert response.json()["mode"]=="observe-and-deliver"
        assert response.json()["delivery"]["queue"][0]["acknowledgedSequence"]==1
        assert "receiver-token" not in response.text
        assert client.get("/_looprd/discovery/demo",headers=ADMIN).json()["deliveryStatus"]["state"]=="acknowledged"
    assert delivery.task.done()

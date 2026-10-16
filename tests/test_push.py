"""DESIGN.adoc / Specification authority / Background push and HTTP synchronization."""

import asyncio
import json
import time

import httpx
import pytest
import sqlalchemy as sa
from starlette.testclient import TestClient

from looprd.app import create_app
from looprd.outbox import EventError
from looprd.push_worker import tick
from looprd.push_transport import topic
from looprd.spec import Specification
from test_database_auth import database_auth, mint
from test_http import BASE, ADMIN


@pytest.fixture
def push_project(setup_project, spec_data, monkeypatch):
    settings, write, _ = setup_project
    database_auth(spec_data)
    spec_data["auth"]["scopes"].append("push:manage")
    spec_data["realtime"] = {"delivery": "durable"}
    spec_data["push"] = {"scopes": ["push:manage"], "maxDevicesPerPrincipal": 2}
    write(spec_data)
    settings.push_public_url = "https://push.example.com"
    settings.push_delivery_url = "http://ntfy:80"
    async def idle(generation):
        await asyncio.sleep(3600)
    monkeypatch.setattr("looprd.push_worker.worker", idle)
    return settings


def store(client):
    return client.app.state.runtime.active["demo"].storage.push


def register(client, headers, installation="device_0001", endpoint="https://push.example.com/up_topic_0001"):
    response = client.post(BASE + "/_push/devices", headers=headers,
                           json={"installationId": installation, "endpoint": endpoint})
    assert response.status_code == 202, response.text
    return response.json()


def challenge(client, identity):
    service = store(client)
    with service.storage.sessions() as session:
        return session.execute(sa.select(service.queue.c.payload).where(service.queue.c.device == identity)).scalar()["challenge"]


def activate(client, headers, **kwargs):
    device = register(client, headers, **kwargs)
    response = client.post(BASE + "/_push/devices/" + device["id"] + "/verify", headers=headers,
                           json={"challenge": challenge(client, device["id"])})
    assert response.status_code == 200, response.text
    return device


def owner(client, headers):
    return client.portal.call(client.app.state.runtime.active["demo"].authenticate, headers["Authorization"]).id


def enqueue(client, headers):
    service = store(client)
    with service.storage.events.transaction() as session:
        return service.notify(session, owner(client, headers))


def test_registration_challenge_ownership_scopes_and_renewal(push_project):
    with TestClient(create_app(push_project)) as client:
        _, first = mint(client, ["push:manage"])
        _, other = mint(client, ["push:manage"])
        _, forbidden = mint(client, ["events:read"])
        assert client.get(BASE + "/_push/devices").status_code == 401
        assert client.get(BASE + "/_push/devices", headers=forbidden).status_code == 403
        device = register(client, first)
        assert "challenge" not in device and "endpoint" not in device
        assert enqueue(client, first) == 0
        path = BASE + "/_push/devices/" + device["id"]
        token = challenge(client, device["id"])
        assert client.post(path + "/verify", headers=other, json={"challenge": token}).status_code == 404
        assert client.post(path + "/verify", headers=first, json={"challenge": "x" * 43}).status_code == 403
        assert client.post(path + "/verify", headers=first, json={"challenge": token}).status_code == 200
        assert client.delete(path, headers=other).status_code == 404
        assert client.get(BASE + "/_push/devices", headers=other).json() == {"devices": []}
        response = client.post(BASE + "/_push/devices", headers=first,
            json={"installationId": "device_0001", "endpoint": "https://push.example.com/up_topic_0001"})
        assert response.status_code == 200 and response.json()["id"] == device["id"]
        assert client.post(BASE + "/_push/devices", headers=other,
            json={"installationId": "device_0002", "endpoint": "https://push.example.com/up_topic_0001"}).status_code == 409
        assert client.delete(path, headers=first).status_code == 200
        assert store(client).status() == {"devices": 0, "queued": 0, "failed": 0}


@pytest.mark.parametrize("endpoint", ["http://push.example.com/topic", "https://evil.example/topic",
    "https://push.example.com@evil.example/topic", "https://push.example.com/a/b", "https://push.example.com/%2e%2e",
    "https://push.example.com/topic?x=y", "https://push.example.com/topic#secret", "https://push.example.com:444/topic",
    "\nhttps://push.example.com/topic"])
def test_endpoint_restrictions(push_project, endpoint):
    with pytest.raises(ValueError):
        topic(push_project, endpoint)


def test_atomic_notify_coalescing_retry_and_registration_rotation(push_project):
    with TestClient(create_app(push_project)) as client:
        _, headers = mint(client, ["push:manage"])
        device = activate(client, headers)
        service = store(client)
        with pytest.raises(RuntimeError):
            with service.storage.events.transaction() as session:
                service.notify(session, owner(client, headers))
                raise RuntimeError("rollback")
        assert service.status()["queued"] == 0
        with service.storage.sessions.begin() as session:
            with pytest.raises(ValueError, match="transaction"):
                service.notify(session, owner(client, headers))
        assert enqueue(client, headers) == 1
        job = service.claim()
        assert job["payload"] == {"apiVersion": "looprd-push/v1", "type": "sync"}
        assert service.claim() is None
        enqueue(client, headers)
        service.finish(job, 200)
        assert service.status()["queued"] == 1  # New hint survived completion of the previous one.
        current = service.claim()
        service.finish(current, 503)
        assert service.claim() is None
        with service.storage.sessions.begin() as session:
            session.execute(service.queue.update().values(due=0))
        retry = service.claim()
        assert retry["attempts"] == 1
        replacement = register(client, headers, endpoint="https://push.example.com/up_replacement")
        assert replacement["id"] == device["id"]
        service.finish(retry, 410)
        assert service.status()["devices"] == 1
        assert challenge(client, device["id"])


def test_delivery_headers_payload_and_revoked_key(push_project):
    calls = []
    def response(request):
        calls.append(request)
        return httpx.Response(200)
    with TestClient(create_app(push_project)) as client:
        metadata, headers = mint(client, ["push:manage"])
        device = register(client, headers)
        transport = httpx.AsyncClient(transport=httpx.MockTransport(response))
        generation = client.app.state.runtime.active["demo"]
        assert client.portal.call(tick, generation, transport)
        assert str(calls[0].url) == "http://ntfy/up_topic_0001"
        assert calls[0].headers["X-UnifiedPush"] == "1"
        payload = json.loads(calls[0].content)
        assert payload["type"] == "verify" and payload["deviceId"] == device["id"]
        assert client.post(BASE + "/_push/devices/" + device["id"] + "/verify", headers=headers,
            json={"challenge": payload["challenge"]}).status_code == 200
        enqueue(client, headers)
        assert client.portal.call(tick, generation, transport)
        assert json.loads(calls[-1].content) == {"apiVersion": "looprd-push/v1", "type": "sync"}
        enqueue(client, headers)
        generation.storage.keys.revoke(metadata["id"])
        assert client.portal.call(tick, generation, transport)
        assert len(calls) == 2 and store(client).status()["devices"] == 0
        client.portal.call(transport.aclose)


def test_restart_expiry_quotas_and_management(push_project):
    with TestClient(create_app(push_project)) as client:
        _, headers = mint(client, ["push:manage"])
        activate(client, headers)
        register(client, headers, installation="device_0002", endpoint="https://push.example.com/up_topic_2")
        response = client.post(BASE + "/_push/devices", headers=headers,
            json={"installationId": "device_0003", "endpoint": "https://push.example.com/up_topic_3"})
        assert response.status_code == 429
        enqueue(client, headers)
        assert client.get("/_looprd/components/demo/push").status_code == 401
        data = client.get("/_looprd/components/demo/push", headers=ADMIN).json()
        assert data["devices"] == 2 and "endpoint" not in json.dumps(data)
    with TestClient(create_app(push_project)) as client:
        service = store(client)
        assert service.status()["devices"] == 2 and service.status()["queued"] == 2
        with service.storage.sessions.begin() as session:
            session.execute(service.devices.update().values(expires=time.time() - 1))
        assert service.status() == {"devices": 0, "queued": 0, "failed": 0}


def test_disabled_provider_and_opt_out(push_project, setup_project, spec_data):
    push_project.push_public_url = None
    push_project.push_delivery_url = None
    with TestClient(create_app(push_project)) as client:
        _, headers = mint(client, ["push:manage"])
        assert client.post(BASE + "/_push/devices", headers=headers,
            json={"installationId": "device_0001", "endpoint": "https://push.example.com/topic"}).status_code == 503
        contract = client.get(BASE + "/openapi.json", headers=headers).json()
        assert contract["x-looprd-push"]["hintOnly"] and "/_sync/items" in contract["paths"]
        spec_data.pop("push")
        setup_project[1](spec_data)
        client.post("/_looprd/reconcile", headers=ADMIN)
        assert client.get(BASE + "/_push/devices", headers=headers).status_code == 404


def test_blocking_permanent_failure_and_explicit_revocation(push_project):
    with TestClient(create_app(push_project)) as client:
        _, headers = mint(client, ["push:manage"])
        activate(client, headers)
        enqueue(client, headers)
        generation = client.app.state.runtime.active["demo"]
        calls = []
        def reject(request):
            calls.append(request)
            return httpx.Response(403)
        transport = httpx.AsyncClient(transport=httpx.MockTransport(reject))
        client.put("/_looprd/components/demo/availability", headers=ADMIN, json={"blocked": True})
        assert client.portal.call(tick, generation, transport) is False and not calls
        client.put("/_looprd/components/demo/availability", headers=ADMIN, json={"blocked": False})
        assert client.portal.call(tick, generation, transport)
        assert store(client).status()["failed"] == 1
        assert client.portal.call(tick, generation, transport) is False
        with store(client).storage.events.transaction() as session:
            assert store(client).revoke(session, owner(client, headers)) == 1
        assert store(client).status()["devices"] == 0
        client.portal.call(transport.aclose)


def test_verified_identity_expiry_and_snapshot_isolation(setup_project, spec_data, monkeypatch):
    from test_verification import verified, HEADERS as VERIFIED
    settings = verified(setup_project, spec_data)
    settings.push_public_url = "https://push.example.com"
    spec_data["push"] = {"scopes": ["events:read"]}
    setup_project[1](spec_data)
    async def idle(generation):
        await asyncio.sleep(3600)
    monkeypatch.setattr("looprd.push_worker.worker", idle)
    with TestClient(create_app(settings)) as client:
        device = register(client, VERIFIED)
        assert time.time() < device["expiresAt"] <= time.time() + 61
        client.post(BASE + "/items", headers=VERIFIED, json={"name": "alice-private"})
        assert len(client.get(BASE + "/_sync/items", headers=VERIFIED).json()["items"]) == 1
        (settings.secrets / "subject").write_text("bob")
        assert client.get(BASE + "/_sync/items", headers=VERIFIED).json()["items"] == []
        assert client.get(BASE + "/_push/devices", headers=VERIFIED).json()["devices"] == []


def test_inline_service_transaction_queues_notification(push_project, setup_project, spec_data):
    push_project.allow_python = True
    spec_data["handlers"] = [{"path": "/notify-self", "method": "POST", "kind": "python", "scopes": ["push:manage"],
        "source": "async def handle(ctx):\n    with ctx.transaction() as session:\n        queued = ctx.notify(session, ctx.principal.id)\n    return {'queued': queued}\n"}]
    setup_project[1](spec_data)
    with TestClient(create_app(push_project)) as client:
        _, headers = mint(client, ["push:manage"])
        activate(client, headers)
        assert client.post(BASE + "/notify-self", headers=headers).json() == {"queued": 1}
        assert store(client).status()["queued"] == 1


@pytest.mark.parametrize("change", [
    lambda spec: spec.update(realtime={"delivery": "best-effort"}),
    lambda spec: spec.update(push={"scopes": ["unknown"]}),
    lambda spec: spec.update(push={"scopes": ["push:manage", "push:manage"]}),
])
def test_invalid_push_contract(push_project, spec_data, change):
    change(spec_data)
    with pytest.raises(ValueError):
        Specification.model_validate(spec_data)

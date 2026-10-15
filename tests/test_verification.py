"""Provider-independent proof verification and generic private-record policies."""

import asyncio
import hashlib
import time

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from looprd.app import create_app
from test_http import BASE, ADMIN

SOURCE = '''
import hmac, time
async def verify(ctx):
    if not hmac.compare_digest(ctx.credential, ctx.secret('proof')):
        return None
    return {'subject': ctx.secret('subject'), 'scopes': ['items:read', 'items:write', 'events:read'],
            'sessionId': 'test-session', 'expiresAt': time.time() + 60}
'''


def verified(setup_project, spec_data, private=True):
    settings, write_spec, _ = setup_project
    settings.allow_python = True
    (settings.secrets / "proof").write_text("server-verified-proof")
    (settings.secrets / "subject").write_text("alice")
    spec_data["auth"] = {"mode": "verified", "realm": "example-accounts",
        "scopes": ["items:read", "items:write", "events:read"], "verifier": {"source": SOURCE}}
    spec_data["resources"]["items"]["permissions"] = {
        op: ["items:read" if op in ("get", "list") else "items:write"] for op in spec_data["resources"]["items"]["operations"]}
    spec_data["websocket"]["permissions"] = {"items": ["events:read"]}
    if private:
        spec_data["models"]["Item"]["fields"]["owner"] = {"type": "string", "maxLength": 64, "required": True}
        spec_data["resources"]["items"]["policy"] = {"ownerField": "owner"}
    spec_data["realtime"] = {"delivery": "durable"}
    write_spec(spec_data)
    return settings


HEADERS = {"Authorization": "Bearer server-verified-proof"}


def test_verified_subject_not_client_user_id_and_owner_scope(setup_project, spec_data):
    settings = verified(setup_project, spec_data)
    with TestClient(create_app(settings)) as client:
        assert client.get(BASE + "/items?userId=alice", headers={"X-User-ID": "alice"}).status_code == 401
        assert client.get(BASE + "/items", headers={"Authorization": "Bearer alice"}).status_code == 401
        expected = hashlib.sha256(b"example-accounts\0alice").hexdigest()
        item = client.post(BASE + "/items?userId=mallory", json={"name": "private"}, headers=HEADERS).json()
        assert item["owner"] == expected
        assert client.post(BASE + "/items", json={"name": "forged", "owner": "mallory"}, headers=HEADERS).status_code == 403
        assert client.patch(BASE + "/items/1", json={"owner": expected}, headers=HEADERS).status_code == 403
        assert len(client.get(BASE + "/_events/items", headers=HEADERS).json()["events"]) == 1
        (settings.secrets / "subject").write_text("bob")
        assert client.get(BASE + "/items", headers=HEADERS).json() == []
        assert client.get(BASE + "/items/1", headers=HEADERS).status_code == 404
        assert client.get(BASE + "/_events/items", headers=HEADERS).json()["events"] == []
        assert client.delete(BASE + "/items/1", headers=HEADERS).status_code == 404


def test_refresh_revocation_and_bad_verifier_fail_closed(setup_project, spec_data):
    settings = verified(setup_project, spec_data)
    with TestClient(create_app(settings)) as client:
        with client.websocket_connect(BASE + "/events", headers=HEADERS) as socket:
            socket.send_json({"subscribe": "items"})
            assert socket.receive_json()["subscribed"] == "items"
            (settings.secrets / "proof").write_text("revoked")
            with pytest.raises(WebSocketDisconnect) as error:
                socket.receive_json()
            assert error.value.code == 1008
    spec_data["auth"]["verifier"]["source"] = "async def verify(ctx): return {'subject': 'unverified', 'scopes': ['unknown']}"
    setup_project[1](spec_data)
    with TestClient(create_app(settings)) as client:
        assert client.get(BASE + "/items", headers=HEADERS).status_code == 401
        status = client.get("/_looprd/status", headers=ADMIN).json()
        assert status["components"]["demo"]["verificationError"] == "ValueError"
        assert "server-verified-proof" not in str(status)


def test_membership_rechecked_for_replay_and_live(setup_project, spec_data):
    settings = verified(setup_project, spec_data, private=False)
    spec_data["models"]["Item"]["fields"]["container"] = {"type": "integer", "required": True}
    spec_data["models"]["Membership"] = {"fields": {
        "id": {"type": "integer", "primaryKey": True, "generated": True},
        "subject": {"type": "string", "maxLength": 64, "required": True},
        "container": {"type": "integer", "required": True}}}
    spec_data["resources"]["items"]["policy"] = {"membership": {"model": "Membership", "subjectField": "subject",
        "membershipField": "container", "recordField": "container"}}
    setup_project[1](spec_data)
    with TestClient(create_app(settings)) as client:
        storage = client.app.state.runtime.active["demo"].storage
        principal_id = hashlib.sha256(b"example-accounts\0alice").hexdigest()
        with storage.sessions.begin() as session:
            session.execute(storage.tables["Membership"].insert().values(subject=principal_id, container=1))
        assert client.post(BASE + "/items", json={"name": "allowed", "container": 1}, headers=HEADERS).status_code == 201
        assert client.post(BASE + "/items", json={"name": "denied", "container": 2}, headers=HEADERS).status_code == 403
        cursor = client.get(BASE + "/_events/items", headers=HEADERS).json()["startCursor"]
        with storage.sessions.begin() as session:
            session.execute(storage.tables["Membership"].delete())
        assert client.get(BASE + "/items", headers=HEADERS).json() == []
        assert client.get(BASE + "/_events/items", headers=HEADERS, params={"after": cursor}).json()["events"] == []


def test_plugin_requires_trusted_python_opt_in(setup_project, spec_data):
    settings = verified(setup_project, spec_data)
    settings.allow_python = False
    with TestClient(create_app(settings)) as client:
        assert client.get(BASE + "/items", headers=HEADERS).status_code == 404
        assert client.get("/_looprd/status", headers=ADMIN).json()["errors"]["demo"] == "load: ValueError"

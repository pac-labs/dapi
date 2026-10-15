"""SQL atomicity, restart, cursor bounds and reconnect behavior."""

import json
import time

import pytest
import sqlalchemy as sa
from starlette.testclient import TestClient

from looprd.app import create_app
from looprd.auth import Principal
from test_http import BASE, HEADERS, ADMIN
from test_database_auth import database_auth, mint


def durable(setup_project, spec_data):
    settings, write_spec, _ = setup_project
    spec_data["realtime"] = {"delivery": "durable", "retentionSeconds": 60, "maxEvents": 100}
    spec_data["requires"] = ["events.replay", "events.transactional"]
    write_spec(spec_data)
    return settings


def test_atomic_events_replay_and_restart(setup_project, spec_data):
    settings = durable(setup_project, spec_data)
    with TestClient(create_app(settings)) as client:
        assert client.get(BASE + "/_events/items").status_code == 401
        for name in ("one", "two", "three"):
            assert client.post(BASE + "/items", json={"name": name}, headers=HEADERS).status_code == 201
        assert client.post(BASE + "/items", json={"name": "one"}, headers=HEADERS).status_code == 409
        page = client.get(BASE + "/_events/items?limit=2", headers=HEADERS).json()
        assert len(page["events"]) == 2 and page["hasMore"]
        cursor = page["cursor"]
        assert len({event["eventId"] for event in page["events"]}) == 2
        assert "record" not in str(page)
    with TestClient(create_app(settings)) as client:
        page = client.get(BASE + "/_events/items", params={"after": cursor}, headers=HEADERS).json()
        assert [event["data"]["name"] for event in page["events"]] == ["three"]
        assert not page["hasMore"]
        assert client.get(BASE + "/_events/items", params={"after": cursor + "x"}, headers=HEADERS).status_code == 400
        with client.websocket_connect(BASE + "/events", headers=HEADERS) as socket:
            socket.send_json({"subscribe": "items", "after": cursor})
            ack = socket.receive_json()
            assert ack["cursor"] == cursor  # Ack must not advance past unsent catch-up events.
            assert socket.receive_json()["data"]["name"] == "three"
            client.post(BASE + "/items", json={"name": "four"}, headers=HEADERS)
            assert socket.receive_json()["data"]["name"] == "four"


def test_transaction_helper_rollback_and_commit(setup_project, spec_data):
    settings = durable(setup_project, spec_data)
    with TestClient(create_app(settings)) as client:
        storage = client.app.state.runtime.active["demo"].storage
        with pytest.raises(RuntimeError):
            with storage.events.transaction() as session:
                session.execute(storage.tables["Item"].insert().values(name="rolled back"))
                storage.events.emit(session, {"resource": "items", "data": {"name": "rolled back"}})
                raise RuntimeError("abort")
        assert client.get(BASE + "/items", headers=HEADERS).json() == []
        assert client.get(BASE + "/_events/items", headers=HEADERS).json()["events"] == []
        with storage.events.transaction() as session:
            storage.events.emit(session, {"resource": "items", "data": {"name": "first"}})
            storage.events.emit(session, {"resource": "items", "data": {"name": "second"}})
        events = client.get(BASE + "/_events/items", headers=HEADERS).json()["events"]
        assert [event["data"]["name"] for event in events] == ["first", "second"]
        with storage.sessions.begin() as session:
            with pytest.raises(ValueError, match="transaction"):
                storage.events.emit(session, {"resource": "items"})


def test_expired_cursor_capacity_and_capabilities(setup_project, spec_data):
    settings = durable(setup_project, spec_data)
    with TestClient(create_app(settings)) as client:
        cursor = client.get(BASE + "/_events/items", headers=HEADERS).json()["cursor"]
        client.post(BASE + "/items", json={"name": "expires"}, headers=HEADERS)
        store = client.app.state.runtime.active["demo"].storage.events
        with store.storage.sessions.begin() as session:
            session.execute(store.events.update().values(created=time.time() - 120))
        response = client.get(BASE + "/_events/items", params={"after": cursor}, headers=HEADERS)
        assert response.status_code == 410 and response.json()["resetRequired"]
        assert client.get("/_looprd/capabilities").status_code == 401
        assert "events.replay" in client.get("/_looprd/capabilities", headers=ADMIN).json()["capabilities"]
        previous = client.app.state.runtime.active["demo"]
        spec_data["requires"] = ["unimplemented.future"]
        setup_project[1](spec_data)
        client.post("/_looprd/reconcile", headers=ADMIN)
        assert client.app.state.runtime.active["demo"] is previous


def test_replay_scopes_identity_binding_and_size_rollback(setup_project, spec_data):
    database_auth(spec_data)
    settings = durable(setup_project, spec_data)
    with TestClient(create_app(settings)) as client:
        _, reader = mint(client, ["items:read", "events:read"])
        _, second_reader = mint(client, ["items:read", "events:read"])
        _, writer = mint(client, ["items:write"])
        assert client.get(BASE + "/_events/items", headers=writer).status_code == 403
        cursor = client.get(BASE + "/_events/items", headers=reader).json()["cursor"]
        assert client.get(BASE + "/_events/items", headers=second_reader, params={"after": cursor}).status_code == 400
        store = client.app.state.runtime.active["demo"].storage
        with pytest.raises(Exception):
            with store.events.transaction() as session:
                session.execute(store.tables["Item"].insert().values(name="oversized"))
                store.events.emit(session, {"resource": "items", "data": "x" * 65536})
        assert client.get(BASE + "/items", headers=reader).json() == []
        assert client.get(BASE + "/_events/items", headers=reader).json()["events"] == []

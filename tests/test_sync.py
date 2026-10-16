"""Reconnect without a socket: snapshot fences, projected replay and deletion tombstones."""

import time

from starlette.testclient import TestClient

from looprd.app import create_app
from test_http import BASE, HEADERS, ADMIN
from test_replay import durable
from test_database_auth import database_auth, mint


def test_snapshot_write_race_and_deletion_recovery(setup_project, spec_data):
    settings = durable(setup_project, spec_data)
    setup_project[1](spec_data)
    with TestClient(create_app(settings)) as client:
        for name in ("one", "two", "three"):
            client.post(BASE + "/items", headers=HEADERS, json={"name": name})
        first = client.get(BASE + "/_sync/items?limit=1", headers=HEADERS).json()
        assert first["items"] == [{"id": 1, "data": {"id": 1, "name": "one"}}] and not first["complete"]
        client.delete(BASE + "/items/1", headers=HEADERS)
        client.patch(BASE + "/items/2", headers=HEADERS, json={"name": "changed"})
        client.post(BASE + "/items", headers=HEADERS, json={"name": "four"})
        second = client.get(BASE + "/_sync/items", headers=HEADERS, params={"cursor": first["next"]}).json()
        assert second["complete"] and [item["id"] for item in second["items"]] == [2, 3]
        assert second["cursor"] == first["cursor"]
        local = {item["id"]: item["data"] for item in first["items"] + second["items"]}
        replay = client.get(BASE + "/_events/items", headers=HEADERS, params={"after": first["cursor"]}).json()
        for event in replay["events"]:
            if event["action"] == "deleted":
                local.pop(event["id"], None)
            else:
                local[event["id"]] = event["data"]
        assert local == {2: {"id": 2, "name": "changed"}, 3: {"id": 3, "name": "three"}, 4: {"id": 4, "name": "four"}}
        assert all("internal" not in event["data"] for event in replay["events"] if event["data"])


def test_projected_snapshot_and_retained_handler_events(setup_project, spec_data):
    settings = durable(setup_project, spec_data)
    spec_data["models"]["Item"]["fields"]["internal"] = {"type": "string", "maxLength": 50}
    spec_data["resources"]["items"].update(operations=["list", "get"], projection=["id", "name"])
    setup_project[1](spec_data)
    with TestClient(create_app(settings)) as client:
        storage = client.app.state.runtime.active["demo"].storage
        with storage.events.transaction() as session:
            session.execute(storage.tables["Item"].insert().values(id=1, name="one", internal="private"))
            storage.events.emit(session, {"resource": "items", "id": 1, "action": "created",
                "data": {"id": 1, "name": "one", "internal": "private"}})
        snapshot = client.get(BASE + "/_sync/items", headers=HEADERS).json()
        assert snapshot["items"] == [{"id": 1, "data": {"id": 1, "name": "one"}}]
        events = client.get(BASE + "/_events/items", headers=HEADERS).json()["events"]
        assert events[0]["data"] == {"id": 1, "name": "one"}


def test_snapshot_cursors_permissions_expiry_and_generation(setup_project, spec_data, monkeypatch):
    database_auth(spec_data)
    settings = durable(setup_project, spec_data)
    with TestClient(create_app(settings)) as client:
        _, reader = mint(client, ["items:read", "events:read"])
        _, second = mint(client, ["items:read", "events:read"])
        _, writer = mint(client, ["items:write"])
        for name in ("one", "two"):
            client.post(BASE + "/items", headers=writer, json={"name": name})
        assert client.get(BASE + "/_sync/items").status_code == 401
        assert client.get(BASE + "/_sync/items", headers=writer).status_code == 403
        page = client.get(BASE + "/_sync/items?limit=1", headers=reader).json()
        assert client.get(BASE + "/_sync/items", headers=second, params={"cursor": page["next"]}).status_code == 400
        assert client.get(BASE + "/_sync/items?limit=101", headers=reader).status_code == 422
        now = time.time()
        with monkeypatch.context() as patch:
            patch.setattr("looprd.sync.time.time", lambda: now + 301)
            expired = client.get(BASE + "/_sync/items", headers=reader, params={"cursor": page["next"]})
            assert expired.status_code == 410 and expired.json()["resetRequired"]
        spec_data["release"] = "new-generation"
        setup_project[1](spec_data)
        client.post("/_looprd/reconcile", headers=ADMIN)
        assert client.get(BASE + "/_sync/items", headers=reader, params={"cursor": page["next"]}).status_code == 410

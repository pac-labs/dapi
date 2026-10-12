"""Database keys protect operations, persist, and revoke live subscriptions."""

import copy

import pytest
import sqlalchemy as sa
from pydantic import ValidationError
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from looprd.app import create_app
from looprd.spec import Specification
from test_http import ADMIN, BASE, HEADERS

KEYS = "/_looprd/components/demo/keys"


def database_auth(spec):
    spec["auth"] = {"mode": "database", "scopes": ["items:read", "items:write", "events:read"]}
    spec["resources"]["items"]["permissions"] = {
        op: ["items:read" if op in ("list", "get") else "items:write"]
        for op in spec["resources"]["items"]["operations"]
    }
    spec["websocket"]["permissions"] = {"items": ["events:read"]}
    return spec


def mint(client, scopes, **kwargs):
    result = client.post(KEYS, headers=ADMIN, json={"label": "test client", "scopes": scopes, **kwargs})
    assert result.status_code == 201, result.text
    key = result.json()
    return key, {"Authorization": "Bearer " + key["apiKey"]}


def test_management_scopes_hashes_and_component_isolation(setup_project, spec_data):
    settings, write_spec, write_sources = setup_project
    write_spec(database_auth(spec_data))
    other = copy.deepcopy(spec_data)
    other["component"] = "other"
    import yaml
    (settings.data.parent / "other.yaml").write_text(yaml.safe_dump(other), encoding="utf-8")
    write_sources([{"id": "demo", "specification": "looprd.yaml"}, {"id": "other", "specification": "other.yaml"}])
    app = create_app(settings)
    with TestClient(app) as client:
        assert client.get(KEYS).status_code == 401
        assert client.post(KEYS, headers=HEADERS, json={}).status_code == 401
        for scopes in (["unknown"], ["items:read", "items:read"], []):
            assert client.post(KEYS, headers=ADMIN, json={"label": "bad", "scopes": scopes}).status_code == 422
        key, reader = mint(client, ["items:read"])
        _, writer = mint(client, ["items:write"])
        assert client.get(BASE + "/items").status_code == 401
        assert client.get(BASE + "/items", headers=HEADERS).status_code == 401
        assert client.post(BASE + "/items", headers=reader, json={"name": "denied"}).status_code == 403
        assert client.get(BASE + "/items", headers=writer).status_code == 403
        created = client.post(BASE + "/items", headers=writer, json={"name": "allowed"})
        assert created.status_code == 201
        assert len(client.get(BASE + "/items", headers=reader).json()) == 1
        for method in ("patch", "delete"):
            assert getattr(client, method)(BASE + "/items/1", headers=reader).status_code == 403
        assert client.get("/looprd/other/v1/items", headers=reader).status_code == 401
        assert client.get(BASE + "/items", headers={"Authorization": reader["Authorization"] + "x"}).status_code == 401
        assert client.get(KEYS, headers=reader).status_code == 401
        listing = client.get(KEYS, headers=ADMIN).json()
        assert "apiKey" not in str(listing) and "digest" not in str(listing)
        assert client.post(BASE + "/_ws-ticket", headers=reader).status_code == 403
        doc = client.get(BASE + "/openapi.json", headers=reader).json()
        assert doc["security"] == [{"bearer": []}]
        assert doc["paths"]["/items"]["post"]["x-looprd-required-scopes"] == ["items:write"]
        store = app.state.runtime.active["demo"].storage
        with store.sessions() as session:
            row = session.execute(sa.select(store.keys.table).where(store.keys.table.c.id == key["id"])).mappings().one()
            assert row["digest"] != key["apiKey"] and len(row["digest"]) == 64
            assert key["apiKey"] not in str(dict(row))


def test_keys_survive_reload_removal_restart_and_revoke(setup_project, spec_data):
    settings, write_spec, write_sources = setup_project
    # Enable database mode on an existing shared-token database without changing its app schema.
    with TestClient(create_app(settings)) as client:
        client.post(BASE + "/items", headers=HEADERS, json={"name": "retained"})
        write_spec(database_auth(spec_data))
        assert not client.post("/_looprd/reconcile", headers=ADMIN).json()["errors"]
        key, headers = mint(client, ["items:read"])
        spec_data["release"] = "2"
        write_spec(spec_data)
        client.post("/_looprd/reconcile", headers=ADMIN)
        assert client.get(BASE + "/items", headers=headers).status_code == 200
        write_sources([])
        client.post("/_looprd/reconcile", headers=ADMIN)
        assert client.get(BASE + "/items", headers=headers).status_code == 404
        write_sources()
        client.post("/_looprd/reconcile", headers=ADMIN)
        assert client.get(BASE + "/items", headers=headers).json()[0]["name"] == "retained"
    with TestClient(create_app(settings)) as client:
        assert client.get(BASE + "/items", headers=headers).status_code == 200
        assert client.delete(KEYS + "/" + key["id"], headers=ADMIN).status_code == 204
        assert client.delete(KEYS + "/" + key["id"], headers=ADMIN).status_code == 204
        assert client.get(BASE + "/items", headers=headers).status_code == 401
    with TestClient(create_app(settings)) as client:
        assert client.get(BASE + "/items", headers=headers).status_code == 401


def test_management_input_and_disabled_access(setup_project, spec_data):
    settings, write_spec, _ = setup_project
    with TestClient(create_app(settings)) as client:
        assert client.get(KEYS, headers=ADMIN).status_code == 409
        assert client.get("/_looprd/components/missing/keys", headers=ADMIN).status_code == 404
        write_spec(database_auth(spec_data))
        client.post("/_looprd/reconcile", headers=ADMIN)
        for expiry in (0, -1, True, "60", 31536001):
            assert client.post(KEYS, headers=ADMIN, json={
                "label": "bad", "scopes": ["items:read"], "expiresInSeconds": expiry,
            }).status_code == 422
        assert client.post(KEYS, headers=ADMIN, content="{").status_code == 422
        assert client.delete(KEYS + "/missing", headers=ADMIN).status_code == 404
        key, _ = mint(client, ["items:read"])
        assert key["expiresAt"] - key["createdAt"] == 2592000
        settings.max_body_bytes = 10
        assert client.post(KEYS, headers=ADMIN, content="x" * 11).status_code == 413
        (settings.secrets / "admin-token").unlink()
        assert client.get(KEYS, headers=ADMIN).status_code == 503


def test_websocket_permissions_ticket_revocation_and_expiry(setup_project, spec_data, monkeypatch):
    settings, write_spec, _ = setup_project
    write_spec(database_auth(spec_data))
    with TestClient(create_app(settings)) as client:
        _, reader = mint(client, ["items:read"])
        key, headers = mint(client, ["events:read"])
        with client.websocket_connect(BASE + "/events", headers=reader) as socket:
            socket.send_json({"subscribe": "items"})
            assert socket.receive_json() == {"error": "insufficient scope"}
        ticket = client.post(BASE + "/_ws-ticket", headers=headers).json()["ticket"]
        with client.websocket_connect(BASE + "/events?ticket=" + ticket) as socket:
            socket.send_json({"subscribe": "items"})
            assert socket.receive_json() == {"subscribed": "items"}
            _, writer = mint(client, ["items:write"])
            client.post(BASE + "/items", headers=writer, json={"name": "event"})
            assert socket.receive_json()["action"] == "created"
            unused_ticket = client.post(BASE + "/_ws-ticket", headers=headers).json()["ticket"]
            client.delete(KEYS + "/" + key["id"], headers=ADMIN)
            with pytest.raises(WebSocketDisconnect) as closed:
                socket.receive_json()
            assert closed.value.code == 1008
        for token in (ticket, unused_ticket):
            with pytest.raises(WebSocketDisconnect):
                with client.websocket_connect(BASE + "/events?ticket=" + token):
                    pass
        expiring, headers = mint(client, ["events:read"], expiresInSeconds=60)
        with client.websocket_connect(BASE + "/events", headers=headers) as socket:
            monkeypatch.setattr("looprd.auth.time.time", lambda: expiring["expiresAt"] + 1)
            assert client.get(BASE + "/openapi.json", headers=headers).status_code == 401
            with pytest.raises(WebSocketDisconnect) as closed:
                socket.receive_json()
            assert closed.value.code == 1008


def test_handler_identity_and_all_required_scopes(setup_project, spec_data):
    settings, write_spec, _ = setup_project
    settings.allow_python = True
    database_auth(spec_data)
    spec_data["handlers"] = [{"path": "/identity", "kind": "python",
                              "scopes": ["items:read", "items:write"],
                              "source": "async def handle(ctx):\n return {'id': ctx.principal.id, 'label': ctx.principal.label}"}]
    write_spec(spec_data)
    with TestClient(create_app(settings)) as client:
        _, partial = mint(client, ["items:read"])
        key, full = mint(client, ["items:read", "items:write"], expiresInSeconds=None)
        assert key["expiresAt"] is None
        assert client.get(BASE + "/identity", headers=partial).status_code == 403
        assert client.get(BASE + "/identity", headers=full).json() == {"id": key["id"], "label": "test client"}


@pytest.mark.parametrize("change", ["database", "operation", "unknown", "websocket", "handler", "public"])
def test_auth_contract_rejects_missing_or_unknown_permissions(spec_data, change):
    database_auth(spec_data)
    if change == "database":
        spec_data.pop("database")
    elif change == "operation":
        spec_data["resources"]["items"]["permissions"].pop("create")
    elif change == "unknown":
        spec_data["resources"]["items"]["permissions"]["create"] = ["unknown"]
    elif change == "websocket":
        spec_data["websocket"].pop("permissions")
    elif change == "handler":
        spec_data["handlers"] = [{"path": "/unprotected", "response": {}}]
    else:
        spec_data["auth"] = {"mode": "public"}
    with pytest.raises(ValidationError):
        Specification.model_validate(spec_data)

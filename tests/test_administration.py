"""Explicit operator grants, transactions, stale-write protection and receipts."""

import copy
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import ValidationError
from starlette.testclient import TestClient

from looprd.app import create_app
from looprd.spec import Specification
from test_http import ADMIN, BASE, HEADERS

ROOT = "/_looprd/data/demo"
ROWS = ROOT + "/resources/items"
ACTION = '''def handle(ctx):
    account = ctx.get('items', ctx.recordId)
    balance = account['coins'] + ctx.body['amount']
    if balance < 0:
        ctx.reject('insufficient_balance')
    ctx.update('items', ctx.recordId, {'coins': balance})
'''


@pytest.fixture
def managed(setup_project, spec_data):
    settings, write_spec, write_sources = setup_project
    settings.allow_python = True
    spec_data["models"]["Item"]["fields"].update(coins={"type": "integer", "required": True},
        role={"type": "string", "maxLength": 10, "required": True}, revision={"type": "integer", "required": True})
    spec_data["resources"]["items"]["revisionField"] = "revision"
    spec_data["management"] = {"resources": {"items": {"label": "Accounts", "titleField": "name", "fields": {
        "name": {"label": "Name", "editable": True}, "coins": {"label": "Coins", "minimum": 0},
        "role": {"label": "Role", "editable": True, "choices": ["player", "admin"]}}}},
        "actions": {"credit": {"label": "Adjust coins", "resource": "items", "writes": {"items": ["coins"]},
            "inputs": {"amount": {"type": "integer", "minimum": -100, "maximum": 100}}, "source": ACTION}}}
    write_spec(spec_data)
    return settings, write_spec, write_sources, spec_data


def insert(client, name="first"):
    response = client.post(BASE + "/items", headers=HEADERS, json={"name": name, "coins": 10, "role": "player"})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def intent(client, identity, values):
    return {"generation": client.get(ROOT, headers=ADMIN).json()["generation"],
            "etag": client.get(ROWS + "/" + str(identity), headers=ADMIN).json()["etag"],
            "operationId": uuid.uuid4().hex, "reason": "Operator support request", "values": values}


def test_disabled_by_default_and_management_credentials_required(setup_project):
    settings, _, _ = setup_project
    with TestClient(create_app(settings)) as client:
        assert client.get("/_looprd/data").status_code == 401
        assert client.get(ROOT, headers=HEADERS).status_code == 401
        assert client.get(ROOT, headers=ADMIN).status_code == 403
        assert client.get("/_looprd/data", headers=ADMIN).json()["components"]["demo"]["enabled"] is False


def test_projected_browsing_and_field_edits_with_audit(managed):
    settings, _, _, _ = managed
    with TestClient(create_app(settings)) as client:
        identity = insert(client)
        catalog = client.get(ROOT, headers=ADMIN).json()
        assert "source" not in catalog["actions"]["credit"] and "def handle" not in str(catalog)
        rows = client.get(ROWS, headers=ADMIN).json()["records"]
        assert rows[0]["values"] == {"name": "first", "coins": 10, "role": "player"}
        assert "revision" not in rows[0]["values"]
        payload = intent(client, identity, {"role": "admin"})
        response = client.patch(ROWS + "/1", headers=ADMIN, json=payload)
        assert response.status_code == 200 and response.json()["changedRecords"] == 1
        record = client.get(BASE + "/items/1", headers=HEADERS).json()
        assert record["role"] == "admin" and record["revision"] == 2
        audit = client.get(ROOT + "/audit", headers=ADMIN).json()["entries"]
        assert len(audit) == 1 and audit[0]["changes"] == [{"resource": "items", "id": 1, "fields": ["role"]}]
        assert "admin" not in str(audit) and audit[0]["actor"] == "management-key"
        repeated = client.patch(ROWS + "/1", headers=ADMIN, json=payload)
        assert repeated.status_code == 200 and repeated.json()["duplicate"] is True
        assert len(client.get(ROOT + "/audit", headers=ADMIN).json()["entries"]) == 1
        assert client.patch(ROWS + "/1", headers=ADMIN, json={**payload, "values": {"role": "player"}}).status_code == 409


def test_actions_enforce_permissions_bounds_and_idempotency(managed):
    settings, _, _, _ = managed
    with TestClient(create_app(settings)) as client:
        identity = insert(client)
        path = ROWS + "/1/actions/credit"
        payload = intent(client, identity, {"amount": 5})
        assert client.post(path, headers=HEADERS, json=payload).status_code == 401
        assert client.post(path, headers=ADMIN, json=payload).status_code == 200
        assert client.post(path, headers=ADMIN, json=payload).json()["duplicate"] is True
        assert client.get(BASE + "/items/1", headers=HEADERS).json()["coins"] == 15
        for values, expected in [({"amount": "5"}, 422), ({"amount": 101}, 422), ({"amount": -100}, 422), ({"amount": 1, "role": "admin"}, 422)]:
            assert client.post(path, headers=ADMIN, json=intent(client, identity, values)).status_code == expected
        for values in [{"coins": 999}, {"revision": 8}, {"id": 2}]:
            assert client.patch(ROWS + "/1", headers=ADMIN, json=intent(client, identity, values)).status_code == 403
        assert client.patch(ROWS + "/1", headers=ADMIN, json=intent(client, identity, {"role": "unknown"})).status_code == 422


def test_stale_generation_and_record_are_rejected(managed):
    settings, write_spec, _, spec = managed
    with TestClient(create_app(settings)) as client:
        identity = insert(client)
        old = intent(client, identity, {"role": "admin"})
        client.patch(BASE + "/items/1", headers={**HEADERS, "If-Match": '"1"'}, json={"name": "changed"})
        assert client.patch(ROWS + "/1", headers=ADMIN, json=old).status_code == 412
        new = intent(client, identity, {"role": "admin"})
        spec["management"]["resources"]["items"]["fields"]["role"]["editable"] = False
        write_spec(spec)
        client.post("/_looprd/reconcile", headers=ADMIN)
        assert client.patch(ROWS + "/1", headers=ADMIN, json=new).status_code == 409


def test_failed_action_rolls_back_records_events_and_audit(managed):
    settings, write_spec, _, spec = managed
    spec["management"]["actions"]["credit"]["source"] = ACTION + "    raise RuntimeError('private failure details')\n"
    write_spec(spec)
    with TestClient(create_app(settings)) as client:
        identity = insert(client)
        response = client.post(ROWS + "/1/actions/credit", headers=ADMIN, json=intent(client, identity, {"amount": 5}))
        assert response.status_code == 500 and "private" not in response.text
        assert client.get(BASE + "/items/1", headers=HEADERS).json()["coins"] == 10
        assert client.get(ROOT + "/audit", headers=ADMIN).json()["entries"] == []


def test_events_and_operator_repair_while_public_api_blocked(managed):
    settings, _, _, _ = managed
    with TestClient(create_app(settings)) as client:
        identity = insert(client)
        with client.websocket_connect(BASE + "/events", headers=HEADERS) as socket:
            socket.send_json({"subscribe": "items"})
            assert socket.receive_json() == {"subscribed": "items"}
            assert client.patch(ROWS + "/1", headers=ADMIN, json=intent(client, identity, {"role": "admin"})).status_code == 200
            assert socket.receive_json()["data"]["role"] == "admin"
        client.put("/_looprd/components/demo/availability", headers=ADMIN, json={"blocked": True})
        assert client.get(BASE + "/items", headers=HEADERS).status_code == 503
        assert client.post(ROWS + "/1/actions/credit", headers=ADMIN, json=intent(client, identity, {"amount": 1})).status_code == 200


def test_pagination_search_restart_and_opt_out_retention(managed):
    settings, write_spec, _, spec = managed
    with TestClient(create_app(settings)) as client:
        for index in range(27):
            insert(client, "account-" + str(index))
        first = client.get(ROWS, headers=ADMIN).json()
        assert len(first["records"]) == 25 and first["nextOffset"] == 25
        assert len(client.get(ROWS + "?offset=25", headers=ADMIN).json()["records"]) == 2
        assert len(client.get(ROWS + "?q=account-26", headers=ADMIN).json()["records"]) == 1
        identity = first["records"][0]["id"]
        payload = intent(client, identity, {"role": "admin"})
        client.patch(ROWS + "/1", headers=ADMIN, json=payload)
    with TestClient(create_app(settings)) as client:
        assert client.patch(ROWS + "/1", headers=ADMIN, json=payload).json()["duplicate"] is True
        original = spec.pop("management")
        write_spec(spec)
        client.post("/_looprd/reconcile", headers=ADMIN)
        assert client.get(ROOT, headers=ADMIN).status_code == 403
        spec["management"] = original
        write_spec(spec)
        client.post("/_looprd/reconcile", headers=ADMIN)
        assert len(client.get(ROOT + "/audit", headers=ADMIN).json()["entries"]) == 1


def test_trusted_python_gate_and_invalid_grants(managed):
    settings, write_spec, _, spec = managed
    settings.allow_python = False
    with TestClient(create_app(settings)) as client:
        assert "demo" not in client.get("/_looprd/status", headers=ADMIN).json()["components"]
    for field in ("id", "revision"):
        bad = copy.deepcopy(spec)
        bad["management"]["resources"]["items"]["fields"][field] = {"editable": True}
        with pytest.raises(ValidationError):
            Specification.model_validate(bad)


def test_concurrent_writes_cannot_overwrite_a_stale_record(managed):
    settings, _, _, _ = managed
    with TestClient(create_app(settings)) as client:
        identity = insert(client)
        first = intent(client, identity, {"role": "admin"})
        second = {**first, "operationId": uuid.uuid4().hex, "values": {"name": "second"}}
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda payload: client.patch(ROWS + "/1", headers=ADMIN, json=payload).status_code, [first, second]))
        assert sorted(results) == [200, 412]
        assert len(client.get(ROOT + "/audit", headers=ADMIN).json()["entries"]) == 1


def test_action_helpers_cannot_write_undeclared_fields(managed):
    settings, write_spec, _, spec = managed
    spec["management"]["actions"]["credit"]["source"] = "def handle(ctx): ctx.update('items', ctx.recordId, {'role': 'admin'})"
    write_spec(spec)
    with TestClient(create_app(settings)) as client:
        identity = insert(client)
        assert client.post(ROWS + "/1/actions/credit", headers=ADMIN, json=intent(client, identity, {"amount": 1})).status_code == 403
        assert client.get(BASE + "/items/1", headers=HEADERS).json()["role"] == "player"
        assert client.get(ROOT + "/audit", headers=ADMIN).json()["entries"] == []


def test_notification_failure_does_not_claim_committed_change_was_rolled_back(managed, monkeypatch):
    settings, _, _, _ = managed
    with TestClient(create_app(settings)) as client:
        identity = insert(client)
        def fail(event):
            raise RuntimeError("transport unavailable")
        monkeypatch.setattr(client.app.state.runtime.active["demo"].bus, "publish", fail)
        result = client.patch(ROWS + "/1", headers=ADMIN, json=intent(client, identity, {"role": "admin"}))
        assert result.status_code == 200 and result.json()["notificationWarning"]
        assert client.get(BASE + "/items/1", headers=HEADERS).json()["role"] == "admin"


def test_shared_model_alias_cannot_edit_ownership_or_revision(managed):
    from test_database_auth import database_auth
    _, _, _, source = managed
    source = database_auth(copy.deepcopy(source))
    source["models"]["Item"]["fields"]["owner"] = {"type": "string", "maxLength": 64}
    source["resources"]["items"]["policy"] = {"ownerField": "owner"}
    alias = copy.deepcopy(source["resources"]["items"])
    alias.update(path="/mirror", policy=None, revisionField=None)
    source["resources"]["mirror"] = alias
    for field in ("owner", "revision"):
        spec = copy.deepcopy(source)
        spec["management"]["resources"]["mirror"] = {"fields": {field: {"editable": True}}}
        with pytest.raises(ValidationError):
            Specification.model_validate(spec)


def test_no_input_management_action(managed):
    settings, write_spec, _, spec = managed
    spec["management"]["actions"]["promote"] = {"label": "Grant admin", "resource": "items", "writes": {"items": ["role"]},
        "source": "def handle(ctx): ctx.update('items', ctx.recordId, {'role': 'admin'})"}
    write_spec(spec)
    with TestClient(create_app(settings)) as client:
        identity = insert(client)
        result = client.post(ROWS + "/1/actions/promote", headers=ADMIN, json=intent(client, identity, {}))
        assert result.status_code == 200
        assert client.get(BASE + "/items/1", headers=HEADERS).json()["role"] == "admin"


def test_record_etags_do_not_expose_plain_hashes_of_hidden_columns(managed):
    import hashlib
    import json
    settings, _, _, _ = managed
    with TestClient(create_app(settings)) as client:
        identity = insert(client)
        record = client.get(ROWS + "/1", headers=ADMIN).json()
        raw = client.get(BASE + "/items/1", headers=HEADERS).json()
        assert record["etag"] != hashlib.sha256(json.dumps(raw, sort_keys=True, allow_nan=False).encode()).hexdigest()
    with TestClient(create_app(settings)) as client:
        assert client.get(ROWS + "/1", headers=ADMIN).json()["etag"] != record["etag"]


def test_audit_failure_rolls_back_the_record(managed, monkeypatch):
    from sqlalchemy.exc import OperationalError
    settings, _, _, _ = managed
    with TestClient(create_app(settings)) as client:
        identity = insert(client)
        def fail(*args):
            raise OperationalError("", {}, RuntimeError("unavailable"))
        monkeypatch.setattr(client.app.state.runtime.active["demo"].storage.operator_audit, "record", fail)
        assert client.patch(ROWS + "/1", headers=ADMIN, json=intent(client, identity, {"role": "admin"})).status_code == 503
        assert client.get(BASE + "/items/1", headers=HEADERS).json()["role"] == "player"


async def test_cancellation_keeps_generation_locked_until_transaction_finishes(managed, monkeypatch):
    import asyncio
    import threading
    from looprd.administration_routes import execute, OperatorWrite
    from looprd.runtime import Runtime
    settings, _, _, _ = managed
    runtime = Runtime(settings)
    await runtime.start()
    release, started = threading.Event(), threading.Event()
    try:
        generation = runtime.active["demo"]
        with generation.storage.engine.begin() as connection:
            connection.execute(generation.storage.tables["Item"].insert().values(name="first",coins=10,role="player",revision=1))
        service = generation.administration
        payload = OperatorWrite(generation=generation.digest, etag=service.read("items", 1)["etag"],
            operationId=uuid.uuid4().hex, reason="Cancellation regression", values={"role":"admin"})
        original = service.write
        def slow(*args):
            started.set()
            assert release.wait(5)
            return original(*args)
        monkeypatch.setattr(service, "write", slow)
        task = asyncio.create_task(execute(runtime, "demo", "items", 1, payload, None))
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        await asyncio.sleep(0)
        assert runtime.lock.locked() and generation.active_requests == 1
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert service.read("items", 1)["values"]["role"] == "admin"
        assert (await execute(runtime, "demo", "items", 1, payload, None))["duplicate"] is True
    finally:
        release.set()
        await runtime.close()


def test_operator_events_honor_client_projection(managed):
    settings, write_spec, _, spec = managed
    spec["resources"]["items"]["operations"] = ["list", "get"]
    spec["resources"]["items"]["projection"] = ["id", "name", "role", "revision"]
    write_spec(spec)
    with TestClient(create_app(settings)) as client:
        generation = client.app.state.runtime.active["demo"]
        with generation.storage.engine.begin() as connection:
            inserted = connection.execute(generation.storage.tables["Item"].insert().values(name="first", coins=10, role="player", revision=1))
            identity = inserted.inserted_primary_key[0]
        with client.websocket_connect(BASE + "/events", headers=HEADERS) as socket:
            socket.send_json({"subscribe": "items"})
            assert socket.receive_json() == {"subscribed": "items"}
            assert client.post(ROWS + "/1/actions/credit", headers=ADMIN, json=intent(client, identity, {"amount": 5})).status_code == 200
            event = socket.receive_json()
            assert event["data"] == {"id": 1, "name": "first", "role": "player", "revision": 2}
            assert "coins" not in event["data"]


def test_committed_receipt_can_be_retrieved_after_grants_change(managed):
    settings, write_spec, _, spec = managed
    with TestClient(create_app(settings)) as client:
        identity = insert(client)
        payload = intent(client, identity, {"role": "admin"})
        assert client.patch(ROWS + "/1", headers=ADMIN, json=payload).status_code == 200
        spec["management"]["resources"]["items"]["fields"]["role"]["editable"] = False
        write_spec(spec)
        client.post("/_looprd/reconcile", headers=ADMIN)
        assert client.patch(ROWS + "/1", headers=ADMIN, json=payload).json()["duplicate"] is True
        assert client.patch(ROWS + "/1", headers=ADMIN, json=intent(client, identity, {"role": "player"})).status_code == 403
        assert len(client.get(ROOT + "/audit", headers=ADMIN).json()["entries"]) == 1

import json
import sqlite3

import httpx

from starlette.testclient import TestClient

from looprd.app import create_app
from looprd.diagnostic_contract import Snapshot
from looprd.diagnostics import Diagnostics
from looprd.failures import explain

ADMIN = {"Authorization": "Bearer admin"}


def test_capability_lifecycle_scope_and_no_application_access(setup_project):
    settings, _, _ = setup_project
    with TestClient(create_app(settings)) as client:
        assert client.get("/_looprd/diagnostics").status_code == 401
        assert client.post("/_looprd/diagnostics", json={}).status_code == 401
        response = client.post("/_looprd/diagnostics", headers=ADMIN, json={"component": "demo"})
        assert response.status_code == 201
        grant = response.json()
        path = grant["path"]
        read = client.get(path)
        assert read.headers["cache-control"] == "no-store"
        data = read.json()
        Snapshot.model_validate(data)
        assert data["scope"] == {"component": "demo", "readOnly": True, "applicationData": False}
        assert [item["id"] for item in data["components"]] == ["demo"]
        assert "repository" not in read.text and "test-token" not in read.text and "allowedOrigins" not in read.text
        assert client.post(path, json={}).status_code == 405
        assert client.get("/_looprd/status", headers={"Authorization": "Bearer " + path.rsplit("/", 1)[1]}).status_code == 401
        listed = client.get("/_looprd/diagnostics", headers=ADMIN).json()
        assert path not in json.dumps(listed)
        assert client.get("/_looprd/diagnostics.openapi.json").json()["openapi"] == "3.1.0"
    # Capabilities survive restarts; only hashes are stored.
    assert path.rsplit("/", 1)[1].encode() not in settings.data.joinpath("diagnostics.sqlite3").read_bytes()
    with TestClient(create_app(settings)) as client:
        assert client.get(path).status_code == 200
        assert client.delete("/_looprd/diagnostics/" + grant["id"], headers=ADMIN).status_code == 200
        assert client.get(path).status_code == 404


def test_expiry_bounds_and_component_scope(setup_project):
    settings, _, _ = setup_project
    with TestClient(create_app(settings)) as client:
        for payload in ({"expiresInSeconds": 43201}, {"expiresInSeconds": True}, {"component": "unknown"}):
            assert client.post("/_looprd/diagnostics", headers=ADMIN, json=payload).status_code in (404, 422)
        grant = client.post("/_looprd/diagnostics", headers=ADMIN, json={}).json()
        with sqlite3.connect(settings.data / "diagnostics.sqlite3") as db:
            db.execute("UPDATE links_v1 SET expires = 0")
        assert client.get(grant["path"]).status_code == 404


def test_migration_explained_and_last_working_generation_retained(setup_project, spec_data):
    settings, write_spec, _ = setup_project
    with TestClient(create_app(settings)) as client:
        spec_data["release"] = "2"
        spec_data["database"].update(schemaVersion=2, migrations=[{"version": 2, "statements": [
            "ALTER TABLE Item ADD COLUMN rollback_probe BOOLEAN", "ALTER TABLE missing_table ADD COLUMN private_secret_sql BOOLEAN"]}])
        write_spec(spec_data)
        client.post("/_looprd/reconcile", headers=ADMIN)
        result = client.get("/_looprd/diagnostics", headers=ADMIN).json()["snapshot"]
        failure = result["failures"][0]
        assert failure["code"] == "migration_failed"
        assert failure["details"]["statementIndex"] == 1
        assert failure["details"]["migrationVersion"] == 2
        assert failure["details"]["reason"] == "table_missing"
        assert result["components"][0]["lastWorkingRetained"]
        assert "private_secret_sql" not in json.dumps(result) and "missing_table" not in json.dumps(result)
        with sqlite3.connect(settings.data / "databases/demo.sqlite3") as db:
            assert "rollback_probe" not in [row[1] for row in db.execute("PRAGMA table_info(Item)")]
    assert Diagnostics(settings.data).failures()[0]["code"] == "migration_failed"


def test_validation_and_unknown_errors_do_not_reveal_secrets(setup_project, spec_data):
    settings, write_spec, _ = setup_project
    with TestClient(create_app(settings)) as client:
        spec_data["private_secret_key"] = "private_secret_value"
        write_spec(spec_data)
        client.post("/_looprd/reconcile", headers=ADMIN)
        runtime = client.app.state.runtime
        runtime.report("demo", "request", RuntimeError("private_exception_secret"))
        runtime.report("hidden-component", "load", RuntimeError("secret"))
        grant = client.post("/_looprd/diagnostics", headers=ADMIN, json={"component": "demo"}).json()
        result = client.get(grant["path"])
        assert all(secret not in result.text for secret in ("private_secret", "private_exception_secret", "hidden-component"))
        assert {item["code"] for item in result.json()["failures"]} == {"operation_failed", "specification_invalid"}


def test_history_bounds_deduplication_and_schema_version_guidance(setup_project, spec_data):
    settings, write_spec, _ = setup_project
    with TestClient(create_app(settings)) as client:
        spec_data["models"]["Item"]["fields"]["extra"] = {"type": "boolean"}
        write_spec(spec_data)
        client.post("/_looprd/reconcile", headers=ADMIN)
        client.post("/_looprd/reconcile", headers=ADMIN)
        failures = client.get("/_looprd/diagnostics", headers=ADMIN).json()["snapshot"]["failures"]
        assert len(failures) == 1 and failures[0]["code"] == "schema_version_required"
        store = client.app.state.runtime.diagnostics
        for number in range(510):
            store.record("demo", "stage-" + str(number), RuntimeError("secret"))
        assert len(store.failures("demo")) == 100
        with store.connect() as db:
            assert db.execute("SELECT COUNT(*) FROM failures_v1").fetchone()[0] == 500
        for _ in range(100):
            store.create("demo", 60)
        import pytest
        with pytest.raises(ValueError):
            store.create("demo", 60)


def test_safe_upstream_reason_and_validation_guidance(setup_project, spec_data):
    request = httpx.Request("GET", "https://private.example.com/private-repo")
    failure = httpx.HTTPStatusError("secret token", request=request, response=httpx.Response(403))
    detail = explain(failure)
    assert detail["code"] == "upstream_http_error" and detail["details"]["httpStatus"] == 403
    assert "secret token" not in json.dumps(detail) and "private.example.com" not in json.dumps(detail)
    settings, write_spec, _ = setup_project
    with TestClient(create_app(settings)) as client:
        spec_data["database"]["schemaVersion"] = 3
        write_spec(spec_data)
        client.post("/_looprd/reconcile", headers=ADMIN)
        detail = client.get("/_looprd/diagnostics", headers=ADMIN).json()["snapshot"]["failures"][0]
        assert detail["details"]["validation"][0]["reason"] == "include ordered migrations 2 through schemaVersion"


def test_diagnostic_store_failure_does_not_stop_components(setup_project):
    settings, _, _ = setup_project
    settings.data.mkdir(parents=True, exist_ok=True)
    (settings.data / "diagnostics.sqlite3").write_bytes(b"damaged database")
    with TestClient(create_app(settings)) as client:
        assert client.get("/_looprd/health").status_code == 200
        assert client.get("/_looprd/diagnostics", headers=ADMIN).status_code == 503
        assert client.post("/_looprd/diagnostics", headers=ADMIN, json={}).status_code == 503
        client.app.state.runtime.report("demo", "request", RuntimeError("secret"))
        assert client.get("/_looprd/status", headers=ADMIN).json()["errors"]["demo"] == "request: RuntimeError"

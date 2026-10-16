"""Central inbox isolation, bounded retention, grouping and diagnostic privacy."""

import copy
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import ValidationError
from starlette.testclient import TestClient

from looprd.app import create_app
from looprd.bugreport_contract import BugReport
from looprd.bugreports import BugReports
from looprd.spec import Specification
from test_database_auth import database_auth, mint
from test_http import ADMIN, BASE, HEADERS

INBOX = "/_looprd/bugreports"
REPORT = {"apiVersion": "looprd-bugreport/v1", "appVersion": "1.2.3", "category": "network",
          "errorCode": "CONNECT_FAILED", "message": "Connection failed", "stackTrace": "at connect (client.js:12:3)",
          "platform": "Windows", "diagnostics": {"attempt": 1}}


def test_submission_auth_private_review_and_deduplication(setup_project):
    settings, _, _ = setup_project
    with TestClient(create_app(settings)) as client:
        assert client.post(BASE + "/_bugreports", json=REPORT).status_code == 401
        receipt = client.post(BASE + "/_bugreports", headers=HEADERS, json=REPORT)
        assert receipt.status_code == 202
        identity = receipt.json()["id"]
        assert receipt.json()["deduplicated"] is False
        repeat = client.post("/dapi/demo/v1/_bugreports", headers=HEADERS, json={**REPORT, "appVersion": "1.2.4", "platform": "Android"})
        assert repeat.json() == {"id": identity, "deduplicated": True, "occurrences": 2}
        assert client.get(BASE + "/_bugreports", headers=HEADERS).status_code == 405
        for method, path in [("GET", INBOX), ("GET", INBOX + "/" + identity),
                             ("PATCH", INBOX + "/" + identity), ("DELETE", INBOX + "/" + identity)]:
            assert client.request(method, path, headers=HEADERS).status_code == 401
        rows = client.get(INBOX, headers=ADMIN).json()["reports"]
        assert len(rows) == 1 and rows[0]["occurrences"] == 2
        assert "stackTrace" not in rows[0] and "diagnostics" not in rows[0]
        detail = client.get(INBOX + "/" + identity, headers=ADMIN).json()
        assert detail["appVersion"] == "1.2.4" and detail["platform"] == "Android"
        assert detail["stackTrace"] == REPORT["stackTrace"]
        assert detail["lastSeenAt"] >= detail["firstSeenAt"]
        assert client.patch(INBOX + "/" + identity, headers=ADMIN, json={"state": "resolved"}).json()["state"] == "resolved"
        assert client.post(BASE + "/_bugreports", headers=HEADERS, json=REPORT).json()["occurrences"] == 3
        assert client.get(INBOX + "/" + identity, headers=ADMIN).json()["state"] == "open"
        grant = client.post("/_looprd/diagnostics", headers=ADMIN, json={}).json()
        assert "CONNECT_FAILED" not in client.get(grant["path"]).text
        assert client.delete(INBOX + "/" + identity, headers=ADMIN).status_code == 204
        assert client.get(INBOX + "/" + identity, headers=ADMIN).status_code == 404
        assert client.delete(INBOX + "/" + identity, headers=ADMIN).status_code == 404


def test_public_cors_blocking_and_generated_contract(setup_project, spec_data):
    settings, write_spec, _ = setup_project
    spec_data["auth"] = {"mode": "public"}
    spec_data["cors"] = {"allowedOrigins": ["http://tauri.localhost"]}
    write_spec(spec_data)
    with TestClient(create_app(settings)) as client:
        preflight = client.options(BASE + "/_bugreports", headers={"Origin": "http://tauri.localhost",
            "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"})
        assert preflight.status_code == 200
        response = client.post(BASE + "/_bugreports", json=REPORT, headers={"Origin": "http://tauri.localhost"})
        assert response.status_code == 202 and response.headers["access-control-allow-origin"] == "http://tauri.localhost"
        contract = client.get(BASE + "/openapi.json").json()
        assert "LooprdBugReport" in contract["components"]["schemas"]
        assert "202" in contract["paths"]["/_bugreports"]["post"]["responses"]
        assert contract["x-looprd-client-requirements"]["bugReports"]["maxBodyBytes"] == 65536
        assert "reports.bugs" in client.get("/_looprd/capabilities", headers=ADMIN).json()["capabilities"]
        client.put("/_looprd/components/demo/availability", headers=ADMIN, json={"blocked": True})
        assert client.post(BASE + "/_bugreports", json=REPORT).status_code == 503


def test_database_principals_can_submit_but_cannot_review(setup_project, spec_data):
    settings, write_spec, _ = setup_project
    write_spec(database_auth(spec_data))
    with TestClient(create_app(settings)) as client:
        key, headers = mint(client, ["items:read"])
        assert client.post(BASE + "/_bugreports", headers=headers, json=REPORT).status_code == 202
        assert client.get(INBOX, headers=headers).status_code == 401
        client.delete("/_looprd/components/demo/keys/" + key["id"], headers=ADMIN)
        assert client.post(BASE + "/_bugreports", headers=headers, json=REPORT).status_code == 401


def test_verified_principal_submission_uses_server_verification(setup_project, spec_data):
    from test_verification import verified, HEADERS as VERIFIED
    settings = verified(setup_project, spec_data, private=False)
    with TestClient(create_app(settings)) as client:
        assert client.post(BASE + "/_bugreports", headers={"X-User-ID": "alice"}, json=REPORT).status_code == 401
        assert client.post(BASE + "/_bugreports", headers=VERIFIED, json=REPORT).status_code == 202


def test_retention_prefers_resolved_and_duplicates_work_at_capacity(tmp_path):
    store = BugReports(tmp_path)
    first = store.submit("demo", BugReport.model_validate(REPORT))["id"]
    for index in range(1, 100):
        store.submit("demo", BugReport.model_validate({**REPORT, "message": "Different failure " + str(index)}))
    rows = store.list()
    assert len(rows) == 100
    resolved = rows[0]["id"]
    store.update(resolved, "resolved")
    store.submit("other", BugReport.model_validate(REPORT))
    assert len(store.list()) == 100 and store.get(resolved) is None and store.get(first)
    assert store.submit("demo", BugReport.model_validate(REPORT))["occurrences"] == 2
    oldest = min(store.list(), key=lambda row: row["lastSeenAt"])["id"]
    store.submit("demo", BugReport.model_validate({**REPORT, "errorCode": "NEW"}))
    assert store.get(oldest) is None and store.get(first)["occurrences"] == 2
    assert len(BugReports(tmp_path).list()) == 100


def test_parallel_duplicate_submissions_preserve_occurrences(tmp_path):
    store = BugReports(tmp_path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        receipts = list(pool.map(lambda _: store.submit("demo", BugReport.model_validate(REPORT)), range(40)))
    assert len({item["id"] for item in receipts}) == 1
    assert store.list()[0]["occurrences"] == 40


def test_sanitization_and_deterministic_grouping(tmp_path):
    store = BugReports(tmp_path)
    sample = {**REPORT, "message": 'Authorization: Bearer never-store-me password="hidden-password" "apiKey": "hidden-key"',
        "stackTrace": "at handler (app.js:12:3) https://name:hidden-userpass@example.com/trace?token=hidden-query#private",
        "diagnostics": {"refreshToken": "hidden-token", "cookie": "hidden-cookie", "trace": "Bearer hidden-bearer", "attempt": 1}}
    receipt = store.submit("demo", BugReport.model_validate(sample))
    detail = store.get(receipt["id"])
    serialized = json.dumps(detail)
    for secret in ["never-store-me", "hidden-password", "hidden-key", "hidden-userpass", "hidden-query", "hidden-token", "hidden-cookie", "hidden-bearer"]:
        assert secret not in serialized
        assert secret.encode() not in store.path.read_bytes()
    changed = copy.deepcopy(sample)
    changed["stackTrace"] = sample["stackTrace"].replace("12:3", "200:50")
    changed["diagnostics"]["attempt"] = 99
    assert store.submit("demo", BugReport.model_validate(changed))["id"] == receipt["id"]
    assert store.submit("other", BugReport.model_validate(sample))["id"] != receipt["id"]
    assert store.submit("demo", BugReport.model_validate({**sample, "errorCode": "OTHER"}))["id"] != receipt["id"]


def test_invalid_bounded_json_and_submission_rate_limits(setup_project):
    settings, _, _ = setup_project
    with TestClient(create_app(settings)) as client:
        for payload in [{**REPORT, "component": "other"}, {**REPORT, "diagnostics": {"nested": {"secret": 1}}},
                        {**REPORT, "diagnostics": {"long": "x" * 1001}}, {**REPORT, "appVersion": 1},
                        {**REPORT, "apiVersion": "future"}, {**REPORT, "message": "x" * 4001}]:
            response = client.post(BASE + "/_bugreports", headers=HEADERS, json=payload)
            assert response.status_code == 422 and "long" not in response.text
        assert client.post(BASE + "/_bugreports", headers=HEADERS, content=b"not-json").status_code == 415
        assert client.post(BASE + "/_bugreports", headers={**HEADERS, "Content-Type": "application/json"}, content=b"x" * 65537).status_code == 413
        assert client.post(BASE + "/_bugreports", headers={**HEADERS, "Content-Type": "application/json"}, content=b'{"diagnostics":{"value":NaN}}').status_code == 422
        for _ in range(21):
            assert client.post(BASE + "/_bugreports", headers=HEADERS, json=REPORT).status_code == 202
        response = client.post(BASE + "/_bugreports", headers=HEADERS, json=REPORT)
        assert response.status_code == 429 and response.headers["retry-after"] == "60"


def test_global_rate_limit_and_expiry(tmp_path, monkeypatch):
    store = BugReports(tmp_path)
    monkeypatch.setattr("looprd.bugreports.time.monotonic", lambda: 100)
    for component in ("a", "b", "c", "d"):
        assert all(store.permit(component) for _ in range(30))
    assert not store.permit("e")
    monkeypatch.setattr("looprd.bugreports.time.monotonic", lambda: 161)
    assert store.permit("a") and len(store.attempts) == 1


def test_reports_persist_through_component_removal_and_restart(setup_project):
    settings, _, write_sources = setup_project
    with TestClient(create_app(settings)) as client:
        identity = client.post(BASE + "/_bugreports", headers=HEADERS, json=REPORT).json()["id"]
        write_sources([])
        client.post("/_looprd/reconcile", headers=ADMIN)
        assert client.post(BASE + "/_bugreports", headers=HEADERS, json=REPORT).status_code == 404
        assert client.get(INBOX + "/" + identity, headers=ADMIN).status_code == 200
    with TestClient(create_app(settings)) as client:
        assert client.get(INBOX + "/" + identity, headers=ADMIN).json()["occurrences"] == 1


def test_storage_failure_is_safe_and_does_not_break_api(setup_project, monkeypatch):
    settings, _, _ = setup_project
    app = create_app(settings)
    with TestClient(app) as client:
        def unavailable(*args):
            raise sqlite3.OperationalError("private database details")
        monkeypatch.setattr(app.state.runtime.bugreports, "submit", unavailable)
        response = client.post(BASE + "/_bugreports", headers=HEADERS, json=REPORT)
        assert response.status_code == 503 and "private" not in response.text
        assert client.get(BASE + "/items", headers=HEADERS).status_code == 200


def test_runtime_route_reserved_and_panel_assets(setup_project, spec_data):
    spec_data["handlers"] = [{"path": "/_bugreports", "response": {}}]
    with pytest.raises(ValidationError):
        Specification.model_validate(spec_data)
    settings, _, _ = setup_project
    with TestClient(create_app(settings)) as client:
        panel = client.get("/_looprd/panel/").text
        assert 'data-view-button="bugreports"' in panel
        assert "Resolve issue" in panel and "Confirm permanent deletion" in panel
        assert "/* PANEL_BUGREPORTS */" not in panel

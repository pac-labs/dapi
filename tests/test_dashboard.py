"""Operational status retains failure context and omits secrets."""

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from looprd.app import create_app
from looprd.runtime import Runtime
from test_http import ADMIN


def test_dashboard_routes_models_runtime_and_live_sockets(setup_project):
    settings, _, _ = setup_project
    with TestClient(create_app(settings)) as client:
        response = client.get("/_looprd/status", headers=ADMIN)
        assert response.headers["cache-control"] == "no-store"
        status = response.json()
        runtime, component = status["runtime"], status["components"]["demo"]
        assert runtime["lastAttemptAt"] and runtime["lastSuccessAt"]
        assert runtime["checking"] is False and runtime["pollSeconds"] == 3600
        assert component["databaseBackend"] == "sqlite"
        assert component["models"]["Item"]["name"]["unique"] is True
        routes = {(r["method"], r["path"]) for r in component["routes"]}
        assert routes == {("GET", "/items"), ("GET", "/items/{id}"), ("POST", "/items"),
                          ("PATCH", "/items/{id}"), ("DELETE", "/items/{id}"),
                          ("GET", "/openapi.json"), ("POST", "/_ws-ticket"), ("WS", "/events")}
        with client.websocket_connect("/looprd/demo/v1/events", headers={"Authorization": "Bearer test-token"}):
            current = client.get("/_looprd/status", headers=ADMIN).json()
            assert current["components"]["demo"]["websocketConnections"] == 1
        assert component["loadedAt"]
        assert "test-token" not in response.text and "secretRef" not in response.text


@pytest.mark.asyncio
async def test_source_failure_preserves_success_and_unloaded_components_visible(setup_project):
    settings, _, write_sources = setup_project
    runtime = Runtime(settings)
    try:
        assert await runtime.reconcile()
        good = runtime.status()["runtime"]["lastSuccessAt"]
        Path(settings.sources).unlink()
        assert not await runtime.reconcile()
        status = runtime.status()
        assert status["runtime"]["lastSuccessAt"] == good
        assert status["runtime"]["lastFinishedAt"]
        assert status["runtime"]["checking"] is False
        assert status["sources"]["demo"]["enabled"]
        write_sources([{"id": "missing", "specification": "missing.yaml"},
                       {"id": "disabled", "enabled": False, "specification": "missing.yaml"}])
        assert await runtime.reconcile()
        status = runtime.status()
        assert "missing" in status["errors"] and "missing" not in status["components"]
        assert status["sources"]["disabled"]["enabled"] is False
    finally:
        await runtime.close()


def test_diagnostics_exclude_inline_code_responses_and_credential_references(setup_project, spec_data):
    settings, write_spec, _ = setup_project
    settings.allow_python = True
    spec_data["handlers"] = [{"path": "/private", "kind": "python",
                              "source": "async def handle(ctx):\n    return 'hidden-handler-content'"},
                             {"path": "/constant", "response": "hidden-response-content"}]
    write_spec(spec_data)
    with TestClient(create_app(settings)) as client:
        response = client.get("/_looprd/status", headers=ADMIN)
        for private in ("hidden-handler-content", "hidden-response-content", "secretRef", "connectionSecret"):
            assert private not in response.text
        assert {r["kind"] for r in response.json()["components"]["demo"]["routes"]} >= {"python", "response"}


@pytest.mark.asyncio
async def test_source_summary_never_exposes_specification_urls(setup_project):
    settings, _, _ = setup_project
    runtime = Runtime(settings)

    class Reader:
        async def sources(self):
            from looprd.spec import Sources
            return Sources.model_validate({"apiVersion": "looprd/v1", "components": [
                {"id": "remote", "enabled": False,
                 "specification": "https://example.com/spec?token=hidden-source-token"}]})

        async def close(self):
            pass

    await runtime.reader.close()
    runtime.reader = Reader()
    try:
        assert await runtime.reconcile()
        assert "hidden-source-token" not in str(runtime.status())
        assert runtime.status()["sources"]["remote"] == {"enabled": False, "repository": None, "ref": None}
    finally:
        await runtime.close()

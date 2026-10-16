import copy

import pytest
import yaml
from pydantic import ValidationError
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from looprd.app import create_app
from looprd.config import Settings
from looprd.spec import Specification
from test_http import ADMIN, BASE, HEADERS

WEB = "https://ui.example.com"
DESKTOP = "http://tauri.localhost"


def preflight(client, origin, base=BASE):
    return client.options(base + "/items", headers={
        "Origin": origin, "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization,content-type,if-match",
    })


def test_component_origins_isolated_and_auth_preserved(setup_project, spec_data):
    settings, write_spec, write_sources = setup_project
    spec_data["cors"] = {"allowedOrigins": [DESKTOP]}
    write_spec(spec_data)
    other = copy.deepcopy(spec_data)
    other.update(component="other", cors={"allowedOrigins": [WEB]})
    with open(settings.data.parent / "other.yaml", "w", encoding="utf-8") as target:
        yaml.safe_dump(other, target)
    write_sources([{"id": "demo", "specification": "looprd.yaml"},
                   {"id": "other", "specification": "other.yaml"}])
    with TestClient(create_app(settings)) as client:
        for base in (BASE, "/dapi/demo/v1"):
            response = preflight(client, DESKTOP, base)
            assert response.status_code == 200
            assert response.headers["access-control-allow-origin"] == DESKTOP
            assert "Origin" in response.headers["vary"]
            denied = client.get(base + "/items", headers={"Origin": DESKTOP})
            assert denied.status_code == 401
            assert denied.headers["access-control-allow-origin"] == DESKTOP
            assert client.get(base + "/items", headers={**HEADERS, "Origin": DESKTOP}).status_code == 200
        assert preflight(client, DESKTOP, "/looprd/other/v1").status_code == 400
        assert preflight(client, WEB).status_code == 400
        response = client.options("/_looprd/status", headers={
            "Origin": DESKTOP, "Access-Control-Request-Method": "GET"})
        assert "access-control-allow-origin" not in response.headers
        with client.websocket_connect(BASE + "/events", headers={**HEADERS, "Origin": DESKTOP}) as socket:
            socket.send_json({"subscribe": "items"})
            assert socket.receive_json() == {"subscribed": "items"}
        for headers in ({"Origin": DESKTOP}, {**HEADERS, "Origin": WEB}):
            with pytest.raises(WebSocketDisconnect):
                with client.websocket_connect(BASE + "/events", headers=headers):
                    pass


def test_reload_failure_retention_empty_policy_and_legacy_fallback(setup_project, spec_data):
    settings, write_spec, _ = setup_project
    settings.allowed_origins = (WEB,)
    with TestClient(create_app(settings)) as client:
        assert preflight(client, WEB).status_code == 200
        assert preflight(client, DESKTOP).status_code == 400
        spec_data["cors"] = {"allowedOrigins": [DESKTOP]}
        write_spec(spec_data)
        client.post("/_looprd/reconcile", headers=ADMIN)
        assert preflight(client, DESKTOP).status_code == 200
        assert preflight(client, WEB).status_code == 200
        spec_data["cors"]["allowedOrigins"] = ["*"]
        write_spec(spec_data)
        client.post("/_looprd/reconcile", headers=ADMIN)
        assert preflight(client, DESKTOP).status_code == 200
        spec_data["cors"]["allowedOrigins"] = []
        write_spec(spec_data)
        client.post("/_looprd/reconcile", headers=ADMIN)
        assert preflight(client, DESKTOP).status_code == 400
        assert preflight(client, WEB).status_code == 200


def test_policy_survives_offline_restart_and_removal(setup_project, spec_data):
    settings, write_spec, write_sources = setup_project
    spec_data["cors"] = {"allowedOrigins": [DESKTOP]}
    write_spec(spec_data)
    with TestClient(create_app(settings)) as client:
        assert preflight(client, DESKTOP).status_code == 200
        assert client.get("/_looprd/status", headers=ADMIN).json()["components"]["demo"]["allowedOrigins"] == [DESKTOP]
    source = settings.data.parent / "sources.yaml"
    source.unlink()
    with TestClient(create_app(settings)) as client:
        assert preflight(client, DESKTOP).status_code == 200
        write_sources([])
        client.post("/_looprd/reconcile", headers=ADMIN)
        assert "access-control-allow-origin" not in preflight(client, DESKTOP).headers
        assert client.get(BASE + "/items", headers=HEADERS).status_code == 404


@pytest.mark.parametrize("restriction", [(WEB,), ()])
def test_operator_restrictions_apply_to_http_and_websocket(setup_project, spec_data, restriction):
    settings, write_spec, _ = setup_project
    settings.allowed_origins = (WEB,)
    settings.origin_restrictions = restriction
    spec_data["cors"] = {"allowedOrigins": [DESKTOP]}
    write_spec(spec_data)
    with TestClient(create_app(settings)) as client:
        assert preflight(client, WEB).status_code == (200 if restriction else 400)
        assert preflight(client, DESKTOP).status_code == 400
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(BASE + "/events", headers={**HEADERS, "Origin": DESKTOP}):
                pass
        # Native requests without an Origin still use normal authentication.
        assert client.get(BASE + "/items", headers=HEADERS).status_code == 200


@pytest.mark.parametrize("origin", ["*", "null", "https://*.example.com", "https://ui.example.com/",
    "https://ui.example.com/path", "https://user@ui.example.com", "https://ui.example.com?",
    "https://ui.example.com#", "https://ui.example.com:99999", " https://ui.example.com"])
def test_invalid_origins_rejected(spec_data, origin):
    spec_data["cors"] = {"allowedOrigins": [origin]}
    with pytest.raises(ValidationError):
        Specification.model_validate(spec_data)


def test_custom_scheme_duplicates_and_env(spec_data, monkeypatch):
    spec_data["cors"] = {"allowedOrigins": ["tauri://localhost", "http://localhost:1420"]}
    Specification.model_validate(spec_data)
    spec_data["cors"]["allowedOrigins"] *= 2
    with pytest.raises(ValidationError):
        Specification.model_validate(spec_data)
    monkeypatch.setenv("LOOPRD_ORIGINS", " https://ui.example.com, http://tauri.localhost ")
    monkeypatch.delenv("LOOPRD_ORIGIN_RESTRICTIONS", raising=False)
    settings = Settings.from_env()
    assert settings.allowed_origins == (WEB, DESKTOP)
    assert settings.origin_restrictions is None
    monkeypatch.setenv("LOOPRD_ORIGIN_RESTRICTIONS", "")
    assert Settings.from_env().origin_restrictions == ()

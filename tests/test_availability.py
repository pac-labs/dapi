"""Operator blocking must survive watchers/restarts and close all API entry points."""

import asyncio
import copy
import json
import threading

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
import yaml

from looprd.app import create_app
from looprd.runtime import Runtime
from test_http import ADMIN, BASE, HEADERS

CONTROL = "/_looprd/components/demo/availability"


def set_blocked(client, blocked):
    result = client.put(CONTROL, headers=ADMIN, json={"blocked": blocked})
    assert result.status_code == 200
    return result.json()


def test_block_persists_across_reload_restart_removal_and_keeps_data(setup_project, spec_data):
    settings, write_spec, write_sources = setup_project
    with TestClient(create_app(settings)) as client:
        assert client.post(BASE + "/items", headers=HEADERS, json={"name": "retained"}).status_code == 201
        assert set_blocked(client, True)["components"]["demo"]["blocked"] is True
        for prefix in ("/looprd", "/dapi"):
            for path in ("/items", "/openapi.json", "/_ws-ticket"):
                response = client.get(prefix + "/demo/v1" + path, headers=HEADERS)
                assert response.status_code == 503
        assert client.post(BASE + "/_ws-ticket", headers=HEADERS).status_code == 503
        spec_data["release"] = "2"
        write_spec(spec_data)
        client.post("/_looprd/reconcile", headers=ADMIN)
        assert client.get(BASE + "/items", headers=HEADERS).status_code == 503
    with TestClient(create_app(settings)) as client:
        assert client.get(BASE + "/items", headers=HEADERS).status_code == 503
        write_sources([])
        client.post("/_looprd/reconcile", headers=ADMIN)
        assert set_blocked(client, False)["components"] == {}
        assert client.get(BASE + "/items", headers=HEADERS).status_code == 404
        write_sources()
        client.post("/_looprd/reconcile", headers=ADMIN)
        assert client.get(BASE + "/items/1", headers=HEADERS).json()["name"] == "retained"
        set_blocked(client, True)
        write_sources([])
        client.post("/_looprd/reconcile", headers=ADMIN)
        write_sources()
        client.post("/_looprd/reconcile", headers=ADMIN)
        assert client.get(BASE + "/items", headers=HEADERS).status_code == 503


def test_management_protection_validation_and_failed_persistence(setup_project, monkeypatch):
    settings, _, _ = setup_project
    with TestClient(create_app(settings)) as client:
        assert client.put(CONTROL, json={"blocked": True}).status_code == 401
        for body in ({"blocked": "false"}, {}, {"blocked": True, "unexpected": 1}):
            assert client.put(CONTROL, headers=ADMIN, json=body).status_code == 422
        assert client.put(CONTROL, headers=ADMIN, content=b"x" * 1025).status_code == 413
        assert client.put(CONTROL.replace("demo", "missing"), headers=ADMIN, json={"blocked": True}).status_code == 404

        def failed_save(blocked):
            raise OSError("test write failure")

        monkeypatch.setattr(client.app.state.runtime.availability, "save", failed_save)
        assert client.put(CONTROL, headers=ADMIN, json={"blocked": True}).status_code == 503
        assert client.get(BASE + "/items", headers=HEADERS).status_code == 200
        assert not (settings.data / "availability.json").exists()


def test_block_closes_live_and_draining_sockets_and_invalidates_tickets(setup_project, spec_data):
    settings, write_spec, _ = setup_project
    settings.drain_seconds = 30
    with TestClient(create_app(settings)) as client:
        with client.websocket_connect(BASE + "/events", headers=HEADERS) as old:
            old.send_json({"subscribe": "items"})
            assert old.receive_json() == {"subscribed": "items"}
            spec_data["release"] = "2"
            write_spec(spec_data)
            client.post("/_looprd/reconcile", headers=ADMIN)
            with client.websocket_connect(BASE + "/events", headers=HEADERS) as current:
                current.send_json({"subscribe": "items"})
                assert current.receive_json() == {"subscribed": "items"}
                ticket = client.post(BASE + "/_ws-ticket", headers=HEADERS).json()["ticket"]
                set_blocked(client, True)
                for socket in (old, current):
                    with pytest.raises(WebSocketDisconnect) as error:
                        socket.receive_json()
                    assert error.value.code == 1008
                with pytest.raises(WebSocketDisconnect):
                    with client.websocket_connect(BASE + "/events", headers=HEADERS):
                        pass
                set_blocked(client, False)
                with pytest.raises(WebSocketDisconnect):
                    with client.websocket_connect(BASE + "/events?ticket=" + ticket):
                        pass


def test_invalid_durable_override_fails_closed(setup_project):
    settings, _, _ = setup_project
    settings.data.mkdir()
    for state in ({"version": 2, "blocked": []}, {"version": 1, "blocked": ["demo", "demo"]}, {"version": 1, "blocked": ["../bad"]}):
        (settings.data / "availability.json").write_text(json.dumps(state))
        with pytest.raises(ValueError):
            Runtime(settings)


def test_other_component_keeps_serving(setup_project, spec_data):
    settings, _, write_sources = setup_project
    other = copy.deepcopy(spec_data)
    other["component"] = "other"
    (settings.data.parent / "other.yaml").write_text(yaml.safe_dump(other), encoding="utf-8")
    write_sources([{"id": "demo", "specification": "looprd.yaml"},
                   {"id": "other", "specification": "other.yaml"}])
    with TestClient(create_app(settings)) as client:
        set_blocked(client, True)
        assert client.get(BASE + "/items", headers=HEADERS).status_code == 503
        assert client.get("/looprd/other/v1/items", headers=HEADERS).status_code == 200


@pytest.mark.asyncio
async def test_cancelled_update_keeps_live_gate_aligned_with_durable_state(setup_project, monkeypatch):
    settings, _, _ = setup_project
    runtime = Runtime(settings)
    await runtime.start()
    entered, release = threading.Event(), threading.Event()
    original = runtime.availability.save

    def delayed_save(blocked):
        entered.set()
        assert release.wait(5)
        original(blocked)

    monkeypatch.setattr(runtime.availability, "save", delayed_save)
    try:
        task = asyncio.create_task(runtime.set_blocked("demo", True))
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert "demo" in runtime.availability.blocked
        assert json.loads((settings.data / "availability.json").read_text())["blocked"] == ["demo"]
    finally:
        release.set()
        await runtime.close()

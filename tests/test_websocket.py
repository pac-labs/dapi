import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from looprd.app import create_app
from looprd.events import EventBus, Tickets
from test_http import ADMIN, BASE, HEADERS


def test_commit_events_and_single_use_ticket(setup_project):
    settings, _, _ = setup_project
    with TestClient(create_app(settings)) as client:
        ticket = client.post(BASE + "/_ws-ticket", headers=HEADERS).json()["ticket"]
        with client.websocket_connect(BASE + "/events?ticket=" + ticket) as socket:
            socket.send_json({"subscribe": "items"})
            assert socket.receive_json() == {"subscribed": "items"}
            client.post(BASE + "/items", headers=HEADERS, json={"name": "event"})
            event = socket.receive_json()
            assert event == {"component": "demo", "resource": "items", "action": "created", "id": 1,
                             "data": {"id": 1, "name": "event"}}
            client.delete(BASE + "/items/1", headers=HEADERS)
            assert socket.receive_json()["data"] is None
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(BASE + "/events?ticket=" + ticket):
                pass


def test_reload_closes_old_socket_with_restart_code(setup_project, spec_data):
    settings, write_spec, _ = setup_project
    with TestClient(create_app(settings)) as client:
        with client.websocket_connect(BASE + "/events", headers=HEADERS) as socket:
            spec_data["release"] = "2"
            write_spec(spec_data)
            client.post("/_looprd/reconcile", headers=ADMIN)
            with pytest.raises(WebSocketDisconnect) as error:
                socket.receive_json()
            assert error.value.code == 1012


def test_bad_origin_and_missing_auth_rejected(setup_project):
    settings, _, _ = setup_project
    with TestClient(create_app(settings)) as client:
        for headers in ({}, {**HEADERS, "Origin": "https://untrusted.example"}):
            with pytest.raises(WebSocketDisconnect):
                with client.websocket_connect(BASE + "/events", headers=headers):
                    pass


def test_ticket_scoped_and_event_queues_bounded():
    tickets = Tickets()
    token = tickets.issue("generation-a")
    assert not tickets.consume(token, "generation-b")
    assert not tickets.consume(token, "generation-a")
    bus = EventBus(1)
    subscriber = bus.subscribe()
    subscriber.resources.add("items")
    bus.publish({"resource": "items"})
    bus.publish({"resource": "items"})
    assert subscriber.overflow

import copy

from starlette.testclient import TestClient

from looprd.app import create_app

HEADERS = {"Authorization": "Bearer test-token"}
ADMIN = {"Authorization": "Bearer admin"}
BASE = "/looprd/demo/v1"


def test_crud_validation_authentication_and_openapi(setup_project):
    settings, _, _ = setup_project
    with TestClient(create_app(settings)) as client:
        assert client.get(BASE + "/items").status_code == 401
        assert client.post(BASE + "/items", headers=HEADERS, json={"name": "first"}).status_code == 201
        assert client.post(BASE + "/items", headers=HEADERS, json={"name": "first"}).status_code == 409
        assert client.post(BASE + "/items", headers=HEADERS, json={"name": "x", "unknown": 1}).status_code == 422
        assert client.post(BASE + "/items", headers=HEADERS, json={"name": "x", "id": 7}).status_code == 422
        assert client.post(BASE + "/items", headers=HEADERS, json={"name": "x" * 31}).status_code == 422
        assert client.get(BASE + "/items/1", headers=HEADERS).json()["name"] == "first"
        assert client.patch(BASE + "/items/1", headers=HEADERS, json={"name": "second"}).status_code == 200
        assert client.patch(BASE + "/items/1", headers=HEADERS, json={"name": None}).status_code == 422
        assert client.get(BASE + "/items?limit=1", headers=HEADERS).json() == [{"id": 1, "name": "second"}]
        document = client.get(BASE + "/openapi.json", headers=HEADERS).json()
        assert document["openapi"] == "3.1.0"
        assert "post" in document["paths"]["/items"]
        assert document["components"]["schemas"]["ItemCreate"]["required"] == ["name"]
        assert client.delete(BASE + "/items/1", headers=HEADERS).status_code == 204
        assert client.get(BASE + "/items/1", headers=HEADERS).status_code == 404


def test_reload_removal_readdition_and_data_preservation(setup_project, spec_data):
    settings, write_spec, write_sources = setup_project
    with TestClient(create_app(settings)) as client:
        client.post(BASE + "/items", headers=HEADERS, json={"name": "persist"})
        spec_data["release"] = "2.0"
        spec_data["handlers"] = [{"path": "/hello", "response": {"message": "updated"}}]
        write_spec(spec_data)
        assert client.post("/_looprd/reconcile", headers=ADMIN).status_code == 200
        assert client.get(BASE + "/hello", headers=HEADERS).json() == {"message": "updated"}
        assert client.get(BASE + "/items/1", headers=HEADERS).json()["name"] == "persist"
        write_sources([])
        client.post("/_looprd/reconcile", headers=ADMIN)
        assert client.get(BASE + "/items", headers=HEADERS).status_code == 404
        write_sources()
        client.post("/_looprd/reconcile", headers=ADMIN)
        assert client.get(BASE + "/items/1", headers=HEADERS).json()["name"] == "persist"
    with TestClient(create_app(settings)) as client:
        assert client.get(BASE + "/items/1", headers=HEADERS).json()["name"] == "persist"


def test_invalid_update_keeps_old_http_routes(setup_project, spec_data):
    settings, write_spec, _ = setup_project
    with TestClient(create_app(settings)) as client:
        spec_data["resources"]["items"]["model"] = "Missing"
        write_spec(spec_data)
        response = client.post("/_looprd/reconcile", headers=ADMIN)
        assert response.json()["errors"]["demo"] == "load: ValidationError"
        assert client.get(BASE + "/items", headers=HEADERS).status_code == 200


def test_management_and_body_limits(setup_project):
    settings, _, _ = setup_project
    settings.max_body_bytes = 32
    with TestClient(create_app(settings)) as client:
        assert client.get("/_looprd/health").json()["service"] == "looprd"
        assert client.get("/_looprd/status").status_code == 401
        assert client.get("/_looprd/status", headers=ADMIN).status_code == 200
        assert client.post(BASE + "/items", headers=HEADERS, content="x" * 33).status_code == 413
        assert client.post(BASE + "/items", headers=HEADERS, content="{").status_code == 400
        assert client.get("/looprd/demox/v1/items", headers=HEADERS).status_code == 404
        assert client.get("/dapi/demo/v1/items", headers=HEADERS).status_code == 200


def test_self_contained_inline_python_handler(setup_project, spec_data):
    settings, write_spec, _ = setup_project
    spec_data["handlers"] = [{"path": "/double", "method": "POST", "kind": "python", "source":
        "async def handle(ctx):\n    return {'value': ctx.body['value'] * 2}\n"}]
    write_spec(spec_data)
    with TestClient(create_app(settings)) as client:
        assert client.get(BASE + "/items", headers=HEADERS).status_code == 404
        assert "demo" in client.get("/_looprd/status", headers=ADMIN).json()["errors"]
    settings.allow_python = True
    with TestClient(create_app(settings)) as client:
        assert client.post(BASE + "/double", headers=HEADERS, json={"value": 6}).json() == {"value": 12}


def test_datetime_and_json_fields(setup_project, spec_data):
    settings, write_spec, _ = setup_project
    spec_data["models"]["Item"]["fields"].update({
        "when": {"type": "datetime", "required": True}, "details": {"type": "json"},
    })
    write_spec(spec_data)
    with TestClient(create_app(settings)) as client:
        response = client.post(BASE + "/items", headers=HEADERS, json={
            "name": "typed", "when": "2026-10-11T12:00:00", "details": {"nested": [1, True]},
        })
        assert response.status_code == 201
        assert response.json()["when"] == "2026-10-11T12:00:00"
        assert response.json()["details"] == {"nested": [1, True]}


def test_updating_one_component_keeps_other_generation(setup_project, spec_data):
    settings, write_spec, write_sources = setup_project
    import yaml
    from pathlib import Path
    other = copy.deepcopy(spec_data)
    other.update(component="other", auth={"mode": "public"})
    Path(settings.sources).with_name("other.yaml").write_text(yaml.safe_dump(other), encoding="utf-8")
    write_sources([{"id": "demo", "specification": "looprd.yaml"}, {"id": "other", "specification": "other.yaml"}])
    app = create_app(settings)
    with TestClient(app) as client:
        old_other = app.state.runtime.active["other"]
        spec_data["release"] = "2"
        write_spec(spec_data)
        client.post("/_looprd/reconcile", headers=ADMIN)
        assert app.state.runtime.active["other"] is old_other
        assert client.get("/looprd/other/v1/items").status_code == 200


def test_inflight_request_finishes_on_old_generation(setup_project, spec_data):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    settings, write_spec, _ = setup_project
    settings.allow_python = True
    spec_data["handlers"] = [{"path": "/slow", "kind": "python", "source": (
        "import asyncio\nasync def handle(ctx):\n"
        "    ctx.request.app.state.started.set()\n"
        "    await asyncio.sleep(0.3)\n"
        "    return {'release': ctx.specification.release}\n"
    )}]
    write_spec(spec_data)
    app = create_app(settings)
    app.state.started = threading.Event()
    with TestClient(app) as client, ThreadPoolExecutor() as pool:
        response = pool.submit(client.get, BASE + "/slow", headers=HEADERS)
        assert app.state.started.wait(5)
        spec_data["release"] = "2"
        write_spec(spec_data)
        client.post("/_looprd/reconcile", headers=ADMIN)
        assert response.result(timeout=5).json() == {"release": "1.0"}
        assert client.get(BASE + "/slow", headers=HEADERS).json() == {"release": "2"}

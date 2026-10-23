"""DESIGN / Independent subcomponents: isolation, mounts and authoritative removal."""

import copy
import json

import httpx
import pytest
import yaml
from starlette.testclient import TestClient

from looprd.app import create_app
from looprd.runtime import Runtime
from looprd.sources import SourceReader
from looprd.spec import Source, Specification


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value), encoding="utf-8")


def family(settings, write_spec, spec_data):
    root = copy.deepcopy(spec_data)
    root["cors"] = {"allowedOrigins": []}
    root["subcomponents"] = [{"source": "games/child/looprd.yaml"}]
    child = copy.deepcopy(spec_data)
    child.update(component="child", auth={"mode": "public"},
                 cors={"allowedOrigins": ["http://tauri.localhost"]})
    child["handlers"] = [{"path": "/ping", "response": "child"}]
    path = settings.data.parent / "games/child/looprd.yaml"
    write_spec(root)
    write(path, child)
    return root, child, path


async def test_independent_storage_reload_failure_removal_restart(setup_project, spec_data):
    settings, write_spec, write_sources = setup_project
    root, child, path = family(settings, write_spec, spec_data)
    runtime = Runtime(settings)
    try:
        await runtime.reconcile()
        assert not runtime.errors
        parent, first = runtime.active["demo"], runtime.active["child"]
        assert first.prefix == "/looprd/demo/child/v1"
        assert first.storage is not parent.storage
        assert (settings.data / "databases/child.sqlite3").exists()
        app = create_app(settings, runtime)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(first.prefix + "/items", json={"name": "saved"})
            assert response.status_code == 201
            assert (await client.get(parent.prefix + "/items")).status_code == 401
            headers = {"Authorization": "Bearer test-token"}
            assert (await client.get(parent.prefix + "/items", headers=headers)).json() == []
            assert (await client.get(first.prefix + "/openapi.json")).json()["servers"][0]["url"] == first.prefix
            assert (await client.get("/looprd/child/v1/ping")).status_code == 404
        child["release"] = "2"
        write(path, child)
        await runtime.reconcile()
        assert runtime.active["demo"] is parent
        second = runtime.active["child"]
        assert second is not first
        path.unlink()
        await runtime.reconcile()
        assert runtime.active["child"] is second and "demo" in runtime.errors
        await runtime.close()
        runtime = Runtime(settings)
        await runtime.restore()
        assert runtime.active["child"].prefix == first.prefix
        await runtime.reconcile()
        assert "child" in runtime.active  # Unreachable child is not withdrawal.
        root["subcomponents"] = []
        write_spec(root)
        await runtime.reconcile()
        assert set(runtime.active) == {"demo"}
        assert (settings.data / "databases/child.sqlite3").exists()
        root["subcomponents"] = [{"source": "games/child/looprd.yaml"}]
        write_spec(root)
        write(path, child)
        await runtime.reconcile()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(settings, runtime)), base_url="http://test") as client:
            assert (await client.get(first.prefix + "/items")).json()[0]["name"] == "saved"
        write_sources([])
        await runtime.reconcile()
        assert not runtime.active and not runtime.tree
    finally:
        await runtime.close()


async def test_nested_includes_registration_and_offline_sources(setup_project, spec_data):
    settings, write_spec, _ = setup_project
    _, child, path = family(settings, write_spec, spec_data)
    child["includes"] = ["routes.yaml"]
    child["subcomponents"] = [{"source": "more/looprd.yaml"}]
    child["registrations"] = [{"service": "atrium", "document": "atriumd.yaml"}]
    write(path, child)
    write(path.parent / "routes.yaml", {"handlers": [{"path": "/fragment", "response": "ok"}]})
    grandchild = {"apiVersion": "looprd/v1", "component": "leaf", "release": "1",
        "api": {"version": "v2"}, "auth": {"mode": "public"}}
    write(path.parent / "more/looprd.yaml", grandchild)
    runtime = Runtime(settings)
    try:
        await runtime.reconcile()
        assert set(runtime.active) == {"demo", "child", "leaf"}
        assert runtime.active["leaf"].prefix == "/looprd/demo/child/leaf/v2"
        assert runtime.reader.snapshots["child"].location == str(path)
        assert len(runtime.active["child"].spec.handlers) == 2
        settings_path = settings.data.parent / "sources.yaml"
        settings_path.unlink()
        await runtime.reconcile()
        assert runtime.sources_error and len(runtime.active) == 3
        await runtime.close()
        runtime = Runtime(settings)
        await runtime.restore()
        await runtime.reconcile()
        assert runtime.active["leaf"].prefix.endswith("/child/leaf/v2")
        assert not runtime.reader.snapshots
    finally:
        await runtime.close()


@pytest.mark.parametrize("kind", ["parent", "sibling", "other-root", "version"])
async def test_collision_rejects_tree_without_overwriting_existing(setup_project, spec_data, kind):
    settings, write_spec, write_sources = setup_project
    runtime = Runtime(settings)
    try:
        await runtime.reconcile()
        before = runtime.active["demo"]
        root, child, path = family(settings, write_spec, spec_data)
        if kind == "parent":
            child["component"] = "demo"
        elif kind == "version":
            child["component"] = "v1"
        elif kind == "sibling":
            root["subcomponents"].append({"source": "games/other.yaml"})
            write(path.parent.parent / "other.yaml", child)
        elif kind == "other-root":
            write_sources([{"id": "demo"}, {"id": "child", "specification": "separate.yaml"}])
            write(settings.data.parent / "separate.yaml", child)
        write(path, child)
        write_spec(root)
        await runtime.reconcile()
        assert runtime.active["demo"] is before and "demo" in runtime.errors
        assert "child" not in runtime.tree
    finally:
        await runtime.close()


@pytest.mark.parametrize("source", ["../outside.yaml", "https://example.com/a", "a\\b", "a/%2e/x", "/a", "a?x"])
def test_invalid_child_paths(spec_data, source):
    with pytest.raises(ValueError):
        Specification.model_validate(dict(spec_data, subcomponents=[{"source": source}]))


def test_duplicate_declarations_and_legacy_serialization(spec_data):
    spec = Specification.model_validate(spec_data)
    assert "subcomponents" not in spec.model_dump()
    with pytest.raises(ValueError, match="duplicate subcomponent"):
        Specification.model_validate(dict(spec_data, subcomponents=[{"source": "a.yaml"}] * 2))


async def test_invalid_migration_and_root_disable_preserve_storage(setup_project, spec_data):
    settings, write_spec, write_sources = setup_project
    root, child, path = family(settings, write_spec, spec_data)
    runtime = Runtime(settings)
    try:
        await runtime.reconcile()
        previous = runtime.active["child"]
        child["database"].update(schemaVersion=2, migrations=[{"version": 2,
            "statements": ["ALTER TABLE Missing ADD COLUMN broken BOOLEAN"]}])
        root["release"] = "parent-update"
        write(path, child)
        write_spec(root)
        await runtime.reconcile()
        assert runtime.active["demo"].spec.release == "parent-update"
        assert runtime.active["child"] is previous and "child" in runtime.errors
        state = json.loads(runtime.checkpoint.read_text(encoding="utf-8"))
        assert state["components"]["child"]["specification"]["database"].get("schemaVersion", 1) == 1
        write_sources([{"id": "demo", "enabled": False}])
        await runtime.reconcile()
        assert not runtime.active and not runtime.tree
        assert (settings.data / "databases/child.sqlite3").exists()
    finally:
        await runtime.close()


def test_service_calls_require_explicit_child_bound_credential(setup_project):
    settings, write_spec, _ = setup_project
    settings.allow_python = True
    (settings.secrets / "service-key").write_text("unprovisioned", encoding="utf-8")
    shape = {"type": "object", "properties": {"value": {"type": "string", "maxLength": 16}}, "required": ["value"]}
    root = {"apiVersion": "looprd/v1", "component": "demo", "release": "1", "api": {"version": "v1"},
        "auth": {"mode": "database", "scopes": ["invoke"]}, "database": {"backend": "sqlite"},
        "subcomponents": [{"source": "games/looprd.yaml"}],
        "handlers": [{"path": "/echo", "method": "POST", "kind": "python",
            "source": "async def handle(ctx): return ctx.body", "scopes": ["invoke"],
            "contract": {"name": "echo", "version": 1, "allowedCallers": ["child"], "request": shape, "response": shape}}]}
    child = {"apiVersion": "looprd/v1", "component": "child", "release": "1", "api": {"version": "v1"},
        "auth": {"mode": "public"},
        "dependencies": [{"component": "demo", "contract": "echo", "version": 1, "credentialSecret": "service-key"}],
        "handlers": [{"path": "/invoke", "method": "POST", "kind": "python",
            "source": "async def handle(ctx): return await ctx.call('demo', 'echo', ctx.body)"}]}
    write_spec(root)
    write(settings.data.parent / "games/looprd.yaml", child)
    admin = {"Authorization": "Bearer admin"}
    with TestClient(create_app(settings)) as client:
        assert not client.app.state.runtime.errors
        assert client.post("/looprd/demo/child/v1/invoke", json={"value": "denied"}).status_code == 403
        key = client.post("/_looprd/components/demo/keys", headers=admin,
            json={"label": "child machine", "service": "child", "scopes": ["invoke"]}).json()
        (settings.secrets / "service-key").write_text(key["apiKey"], encoding="utf-8")
        response = client.post("/looprd/demo/child/v1/invoke", json={"value": "accepted"})
        assert response.status_code == 200 and response.json() == {"value": "accepted"}
        client.delete("/_looprd/components/demo/keys/" + key["id"], headers=admin)
        assert client.post("/looprd/demo/child/v1/invoke", json={"value": "revoked"}).status_code == 403


def test_child_registration_reader_uses_child_root_and_nested_runtime(setup_project, spec_data):
    settings, write_spec, _ = setup_project
    _, child, path = family(settings, write_spec, spec_data)
    child["registrations"] = [{"service": "catalog", "document": "metadata.yaml"}]
    write(path, child)
    (path.parent / "metadata.yaml").write_text("opaque: child-owned\n", encoding="utf-8", newline="\n")
    token = "test-only-reader-" + "x" * 32
    (settings.secrets / "reader").write_text(token, encoding="utf-8")
    settings.registration_readers = json.dumps({"catalog": "reader"})
    settings.public_url = "https://api.example.com"
    with TestClient(create_app(settings)) as client:
        headers = {"Authorization": "Bearer " + token}
        result = client.get("/_looprd/registrations", headers=headers).json()
        assert result["complete"] is True
        entry = result["registrations"][0]
        assert entry["component"] == "child"
        assert entry["apiBaseUrl"] == "https://api.example.com/looprd/demo/child/v1"
        body = client.get(entry["documentPath"], params={"generation": entry["generation"]}, headers=headers).json()
        assert body["content"] == "opaque: child-owned\n" and body["source"]["kind"] == "local"


def test_child_cors_websocket_and_operator_block(setup_project, spec_data):
    settings, write_spec, _ = setup_project
    family(settings, write_spec, spec_data)
    with TestClient(create_app(settings)) as client:
        child = "/looprd/demo/child/v1"
        headers = {"Origin": "http://tauri.localhost", "Access-Control-Request-Method": "GET"}
        assert client.options(child + "/items", headers=headers).headers["access-control-allow-origin"] == "http://tauri.localhost"
        assert client.options("/looprd/demo/v1/items", headers=headers).status_code == 400
        assert client.get(child.replace("/looprd/", "/dapi/") + "/ping").json() == "child"
        with client.websocket_connect(child + "/events", headers={"Origin": "http://tauri.localhost"}) as socket:
            socket.send_json({"subscribe": "items"})
            assert socket.receive_json() == {"subscribed": "items"}
            client.put("/_looprd/components/child/availability", json={"blocked": True}, headers={"Authorization": "Bearer admin"})
            assert socket.receive()["type"] == "websocket.close"
        assert client.get(child + "/ping").status_code == 503
        assert client.get("/looprd/demo/v1/items", headers={"Authorization": "Bearer test-token"}).status_code == 200


@pytest.mark.parametrize("transport", ["https", "git"])
async def test_child_source_provenance_and_pinned_commit(tmp_path, spec_data, transport):
    sha, calls = "a" * 40, []
    root = dict(spec_data, subcomponents=[{"source": "games/looprd.yaml"}])
    child = dict(spec_data, component="child")
    def respond(request):
        calls.append(request.url)
        if "/commits/" in request.url.path:
            return httpx.Response(200, text=sha)
        return httpx.Response(200, text=yaml.safe_dump(child if "/games/" in request.url.path else root))
    reader = SourceReader(str(tmp_path / "sources.yaml"))
    await reader.client.aclose()
    reader.client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    source = Source(id="demo", repository="https://github.com/example/demo") if transport == "git" else Source(id="demo", specification="https://example.com/looprd.yaml")
    from types import SimpleNamespace
    from looprd.component_tree import collect
    from looprd.spec import Sources
    context = SimpleNamespace(reader=reader, active={}, tree={}, revisions={})
    try:
        result = await collect(context, Sources(apiVersion="looprd/v1", components=[source]))
        assert not result[3] and set(result[0]) == {"demo", "child"}
        provenance = reader.snapshots["child"].provenance()
        assert provenance["kind"] == transport
        assert provenance["revision"] == (sha if transport == "git" else None)
        if transport == "git":
            assert sum("/commits/" in url.path for url in calls) == 1
            assert all(url.params["ref"] == sha for url in calls if "/contents/" in url.path)
    finally:
        await reader.close()

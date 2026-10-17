"""DESIGN.adoc / Optional discovery observation and service availability."""

from pathlib import Path

import httpx
import pytest
import yaml
from starlette.testclient import TestClient

from looprd.app import create_app
from looprd.runtime import Runtime
from looprd.sources import SourceReader
from looprd.atrium_sidecar import validate
from looprd.discovery_source import SourceSnapshot, observe
from test_http import ADMIN


def sidecar(component="demo"):
    return {"schema": "atriumd/v1", "component": component, "game": {"id": "sample.game", "publisher": "sample-publisher",
        "title": "Sample game", "kind": "game", "platforms": [{"runtime": "web", "formFactors": ["phone", "desktop"], "installMode": "none"}],
        "launch": {"mode": "hosted-web", "originRef": "game-web", "entrypoint": "index.html"}}}


async def settle(runtime):
    if runtime.discovery and runtime.discovery.task:
        await runtime.discovery.task


async def test_default_off_and_local_lifecycle(setup_project):
    settings, _, write_sources = setup_project
    location = Path(settings.sources).parent / "atriumd.yaml"
    location.write_text(yaml.safe_dump(sidecar()))
    runtime = Runtime(settings)
    await runtime.reconcile()
    assert runtime.discovery is None and not (settings.data / "discovery.sqlite3").exists()
    await runtime.close()
    settings.discovery_enabled = True
    runtime = Runtime(settings)
    try:
        await runtime.reconcile(); await settle(runtime)
        generation = runtime.active["demo"]
        first = runtime.discovery.store.get("demo")
        assert first["state"] == "valid" and first["delivery"] == "disabled"
        await runtime.reconcile(); await settle(runtime)
        assert runtime.discovery.store.get("demo")["changedAt"] == first["changedAt"]
        changed = sidecar(); changed["game"]["title"] = "Changed metadata only"
        location.write_text(yaml.safe_dump(changed))
        await runtime.reconcile(); await settle(runtime)
        second = runtime.discovery.store.get("demo")
        assert runtime.active["demo"] is generation
        assert second["lastValid"]["fingerprint"] != first["lastValid"]["fingerprint"]
        location.write_text("invalid: document")
        await runtime.reconcile(); await settle(runtime)
        invalid = runtime.discovery.store.get("demo")
        assert invalid["state"] == "invalid" and invalid["lastValid"] == second["lastValid"]
        location.unlink()
        await runtime.reconcile(); await settle(runtime)
        assert runtime.discovery.store.get("demo")["state"] == "withdrawn"
        assert runtime.active["demo"] is generation and not runtime.errors
        write_sources([])
        await runtime.reconcile(); await settle(runtime)
        assert runtime.discovery.store.get("demo")["state"] == "inactive"
        assert (settings.data / "databases/demo.sqlite3").exists()
    finally:
        await runtime.close()


async def test_missing_unavailable_and_checkpoint_failure(setup_project, spec_data, monkeypatch):
    settings, write, _ = setup_project
    settings.discovery_enabled = True
    runtime = Runtime(settings)
    try:
        await runtime.reconcile(); await settle(runtime)
        assert runtime.discovery.store.get("demo")["state"] == "absent"
        (Path(settings.sources).parent / "atriumd.yaml").write_text(yaml.safe_dump(sidecar()))
        await runtime.reconcile(); await settle(runtime)
        last = runtime.discovery.store.get("demo")
        spec_data["release"] = "2"; write(spec_data)
        def fail(*args): raise OSError("private test failure")
        with monkeypatch.context() as patch:
            patch.setattr(runtime, "save", fail)
            assert not await runtime.reconcile()
            await settle(runtime)
            assert runtime.discovery.store.get("demo") == last
        (Path(settings.sources).parent / "looprd.yaml").unlink()
        await runtime.reconcile(); await settle(runtime)
        row = runtime.discovery.store.get("demo")
        assert row["state"] == "unavailable" and row["lastValid"] == last["lastValid"]
    finally:
        await runtime.close()


async def test_github_same_revision_and_ambiguous_absence(setup_project, spec_data):
    settings, _, write_sources = setup_project
    settings.discovery_enabled = True
    write_sources([{"id": "demo", "repository": "https://github.com/example/game", "specification": "api/looprd.yaml"}])
    calls, revision, mode = [], ["a" * 40], ["valid"]
    def respond(request):
        calls.append(str(request.url))
        if "/commits/" in request.url.path:
            return httpx.Response(200, text=revision[0])
        assert request.url.params["ref"] == revision[0]
        if request.url.path.endswith("looprd.yaml"):
            return httpx.Response(200, text=yaml.safe_dump(spec_data))
        if request.url.path.endswith("atriumd.yaml"):
            return httpx.Response(200, text=yaml.safe_dump(sidecar())) if mode[0] == "valid" else httpx.Response(404)
        if mode[0] == "forbidden":
            return httpx.Response(403)
        return httpx.Response(200, json=[{"name": "looprd.yaml"}])
    reader = SourceReader(settings.sources)
    await reader.client.aclose()
    reader.client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    runtime = Runtime(settings, reader)
    try:
        await runtime.reconcile(); await settle(runtime)
        old = runtime.active["demo"]
        assert runtime.discovery.store.get("demo")["state"] == "valid"
        assert sum("/commits/" in value for value in calls) == 1
        revision[0] = "b" * 40
        await runtime.reconcile(); await settle(runtime)
        assert runtime.active["demo"] is old
        assert runtime.discovery.store.get("demo")["revision"] == revision[0]
        mode[0] = "forbidden"
        await runtime.reconcile(); await settle(runtime)
        assert runtime.discovery.store.get("demo")["state"] == "unavailable"
        mode[0] = "missing"; revision[0] = "c" * 40
        await runtime.reconcile(); await settle(runtime)
        assert runtime.discovery.store.get("demo")["state"] == "withdrawn"
        assert not runtime.errors
    finally:
        await runtime.close()


async def test_sidecar_failure_is_nonblocking_and_manager_is_protected(setup_project, monkeypatch):
    settings, _, _ = setup_project
    settings.discovery_enabled = True
    with TestClient(create_app(settings)) as client:
        runtime = client.app.state.runtime
        client.portal.call(settle, runtime)
        assert client.get("/_looprd/discovery").status_code == 401
        assert client.get("/_looprd/discovery", headers=ADMIN).json()["mode"] == "observe-only"
        assert client.get("/_looprd/discovery/demo", headers=ADMIN).json()["state"] == "absent"
        def fail(*args): raise OSError("private disk failure")
        monkeypatch.setattr(runtime.discovery, "schedule", fail)
        assert client.post("/_looprd/reconcile", headers=ADMIN).status_code == 200
        assert "demo" in runtime.active and not runtime.errors


@pytest.mark.parametrize("change", [
    lambda doc: doc.update(component="different"),
    lambda doc: doc["game"].update(agentPlaySupported=True),
    lambda doc: doc["game"]["launch"].update(originRef="https://private.example"),
    lambda doc: doc["game"]["launch"].update(entrypoint="../private"),
    lambda doc: doc["game"].update(title="https://private.example"),
    lambda doc: doc["game"].update(integration={"atriumOptional": 1, "sessions": "game-owned", "state": "game-owned", "identity": "game-owned"}),
    lambda doc: doc["game"]["platforms"][0].update(installMode="native"),
])
def test_metadata_contract_rejects_unsupported_declarations(change):
    doc = sidecar(); change(doc)
    with pytest.raises(ValueError):
        validate(doc, "demo")


async def test_https_requires_host_policy_and_bounds_fetch(setup_project):
    settings, _, _ = setup_project
    calls = []
    class Reader:
        async def read(self, location, *, max_bytes):
            calls.append((location, max_bytes))
            return yaml.safe_dump(sidecar())
    snapshot = SourceSnapshot("https://metadata.example.com/api/looprd.yaml", "content", "digest")
    assert (await observe(Reader(), snapshot, "demo", settings))["state"] == "unavailable"
    assert not calls
    settings.discovery_https_origins = ("https://metadata.example.com",)
    assert (await observe(Reader(), snapshot, "demo", settings))["state"] == "valid"
    assert calls == [("https://metadata.example.com/api/atriumd.yaml", 65536)]


async def test_observations_survive_restart_and_storage_failure_is_nonblocking(setup_project, monkeypatch):
    settings, _, _ = setup_project
    settings.discovery_enabled = True
    (Path(settings.sources).parent / "atriumd.yaml").write_text(yaml.safe_dump(sidecar()))
    runtime = Runtime(settings)
    await runtime.reconcile(); await settle(runtime)
    saved = runtime.discovery.store.get("demo")
    await runtime.close()
    runtime = Runtime(settings)
    try:
        assert runtime.discovery.store.get("demo") == saved
        def fail(*args): raise OSError("private storage failure")
        monkeypatch.setattr(runtime.discovery.store, "record", fail)
        assert await runtime.reconcile()
        await settle(runtime)
        assert "demo" in runtime.active and not runtime.errors
        assert runtime.discovery.store.get("demo") == saved
    finally:
        await runtime.close()

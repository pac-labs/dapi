"""DESIGN.adoc / Specification authority / Nested specification imports."""

import copy
import json
from pathlib import Path

import httpx
import pytest
import yaml

from looprd.runtime import Runtime
from looprd.sources import SourceReader
from looprd.spec import Source


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value), encoding="utf-8")


async def test_recursive_import_reload_failure_and_checkpoint(setup_project, spec_data):
    settings, write_spec, _ = setup_project
    root = copy.deepcopy(spec_data)
    root["includes"] = ["api/looprd.yaml"]
    root.pop("resources")
    write_spec(root)
    folder = Path(settings.sources).parent / "api"
    write(folder / "looprd.yaml", {"resources": spec_data["resources"], "includes": ["nested/looprd.yaml"]})
    child = folder / "nested/looprd.yaml"
    write(child, {"handlers": [{"path": "/ping", "response": "first"}]})
    runtime = Runtime(settings)
    try:
        await runtime.reconcile()
        assert not runtime.errors
        first = runtime.active["demo"]
        assert first.spec.handlers[0].response == "first"
        write(child, {"handlers": [{"path": "/ping", "response": "second"}]})
        await runtime.reconcile()
        second = runtime.active["demo"]
        assert second is not first
        assert second.spec.handlers[0].response == "second"
        child.unlink()
        await runtime.reconcile()
        assert runtime.active["demo"] is second and runtime.errors
        state = json.loads(runtime.checkpoint.read_text())
        assert "includes" not in state["components"]["demo"]["specification"]
        await runtime.close()
        runtime = Runtime(settings)
        await runtime.restore()
        assert runtime.active["demo"].spec.handlers[0].response == "second"
        # Explicitly removing the include removes its API route, retaining storage.
        root["includes"] = []
        root["resources"] = spec_data["resources"]
        write_spec(root)
        await runtime.reconcile()
        assert not runtime.errors and not runtime.active["demo"].spec.handlers
        assert (settings.data / "databases/demo.sqlite3").exists()
    finally:
        await runtime.close()


@pytest.mark.parametrize("relative", ["../secret.yaml", "/absolute.yaml", "https://evil/spec", "a\\b", "a/%2e%2e/x", "a?x", "./a", "a//b"])
async def test_reject_escaping_paths(tmp_path, relative):
    write(tmp_path / "looprd.yaml", {"includes": [relative]})
    reader = SourceReader(str(tmp_path / "sources.yaml"))
    try:
        with pytest.raises(ValueError, match="relative descendant"):
            await reader.component(Source(id="demo"))
    finally:
        await reader.close()


@pytest.mark.parametrize("fragment", [
    {"release": "override"}, {"auth": {"mode": "public"}},
    {"handlers": [{"path": "/items", "response": "conflict"}]},
    {"models": {"Item": {"fields": {"name": {"type": "integer"}}}}},
    {"unknown": True},
])
async def test_conflicts_and_invalid_imports(tmp_path, spec_data, fragment):
    write(tmp_path / "looprd.yaml", dict(spec_data, includes=["child.yaml"]))
    write(tmp_path / "child.yaml", fragment)
    reader = SourceReader(str(tmp_path / "sources.yaml"))
    try:
        with pytest.raises(ValueError):
            await reader.component(Source(id="demo"))
    finally:
        await reader.close()


async def test_duplicate_import_and_depth_limit(tmp_path, spec_data):
    write(tmp_path / "looprd.yaml", dict(spec_data, includes=["child.yaml", "child.yaml"]))
    write(tmp_path / "child.yaml", {})
    reader = SourceReader(str(tmp_path / "sources.yaml"))
    try:
        with pytest.raises(ValueError, match="repeated"):
            await reader.component(Source(id="demo"))
        write(tmp_path / "looprd.yaml", dict(spec_data, includes=["child.yaml"]))
        path = tmp_path
        for _ in range(10):
            write(path / "child.yaml", {"includes": ["nested/child.yaml"]})
            path /= "nested"
        with pytest.raises(ValueError, match="depth/file"):
            await reader.component(Source(id="demo"))
    finally:
        await reader.close()


@pytest.mark.parametrize("imports", [None, "child.yaml", ["child.yaml"] * 33])
async def test_invalid_include_list(tmp_path, imports):
    write(tmp_path / "looprd.yaml", {"includes": imports})
    reader = SourceReader(str(tmp_path / "sources.yaml"))
    try:
        with pytest.raises(ValueError, match="includes must"):
            await reader.component(Source(id="demo"))
    finally:
        await reader.close()


async def test_aggregate_limit(tmp_path, spec_data):
    write(tmp_path / "looprd.yaml", dict(spec_data, includes=["a.yaml", "b.yaml"]))
    for name in ("a", "b"):
        write(tmp_path / f"{name}.yaml", {"handlers": [{"path": f"/{name}", "response": "x" * 600000}]})
    reader = SourceReader(str(tmp_path / "sources.yaml"))
    try:
        with pytest.raises(ValueError, match="combined specification"):
            await reader.component(Source(id="demo"))
    finally:
        await reader.close()


@pytest.mark.parametrize("location", ["repository", "github", "https"])
async def test_remote_imports_pin_revision_and_origin(spec_data, location):
    sha = "a" * 40
    calls = []
    def respond(request):
        calls.append(str(request.url))
        if "/commits/" in request.url.path:
            return httpx.Response(200, text=sha)
        if request.url.path.endswith("root.yaml"):
            root = copy.deepcopy(spec_data)
            root.pop("resources")
            root["includes"] = ["api/looprd.yaml"]
            return httpx.Response(200, text=yaml.safe_dump(root))
        return httpx.Response(200, text=yaml.safe_dump({"resources": spec_data["resources"]}))
    reader = SourceReader("https://example.com/sources.yaml")
    await reader.client.aclose()
    reader.client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    source = Source(id="demo", specification="root.yaml", repository="https://github.com/example/demo")
    if location == "github":
        source = Source(id="demo", specification="github://example/demo/root.yaml?ref=main")
    elif location == "https":
        source = Source(id="demo", specification="https://example.com/spec/root.yaml")
    try:
        spec, revision = await reader.component(source)
        assert spec.resources and not spec.includes
        if location != "https":
            assert revision == sha
            assert sum("/commits/" in url for url in calls) == 1
            assert all("ref=" + sha in url for url in calls if "/contents/" in url)
        else:
            assert calls[-1] == "https://example.com/spec/api/looprd.yaml"
    finally:
        await reader.close()

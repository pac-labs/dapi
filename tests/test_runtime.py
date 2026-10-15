from pathlib import Path

import httpx
import pytest

from looprd.runtime import Runtime
from looprd.sources import SourceReader


async def test_unreachable_sources_and_specs_keep_server(setup_project):
    settings, write_spec, write_sources = setup_project
    runtime = Runtime(settings)
    try:
        assert await runtime.reconcile()
        current = runtime.active["demo"]
        Path(settings.sources).unlink()
        assert not await runtime.reconcile()
        assert runtime.active["demo"] is current
        write_sources()
        Path(settings.sources).with_name("looprd.yaml").unlink()
        assert await runtime.reconcile()
        assert runtime.active["demo"] is current
        assert "demo" in runtime.errors
    finally:
        await runtime.close()


async def test_malformed_sources_never_removes(setup_project):
    settings, _, _ = setup_project
    runtime = Runtime(settings)
    try:
        await runtime.reconcile()
        Path(settings.sources).write_text("apiVersion: looprd/v1\ncomponents: [broken", encoding="utf-8")
        assert not await runtime.reconcile()
        assert "demo" in runtime.active
    finally:
        await runtime.close()


async def test_explicit_removal_and_disable_persist_after_restart(setup_project):
    settings, _, write_sources = setup_project
    runtime = Runtime(settings)
    await runtime.reconcile()
    database = settings.data / "databases/demo.sqlite3"
    assert database.exists()
    write_sources([])
    assert await runtime.reconcile()
    assert not runtime.active
    assert database.exists()
    await runtime.close()
    runtime = Runtime(settings)
    await runtime.restore()
    assert not runtime.active
    write_sources()
    await runtime.reconcile()
    assert "demo" in runtime.active
    write_sources([{"id": "demo", "specification": "looprd.yaml", "enabled": False}])
    await runtime.reconcile()
    assert not runtime.active and database.exists()
    await runtime.close()


async def test_offline_restart_restores_last_good(setup_project):
    settings, _, _ = setup_project
    runtime = Runtime(settings)
    await runtime.reconcile()
    await runtime.close()
    Path(settings.sources).unlink()
    runtime = Runtime(settings)
    await runtime.start()
    assert "demo" in runtime.active
    assert runtime.sources_error
    await runtime.close()


async def test_schema_change_without_migration_preserves_generation(setup_project, spec_data):
    settings, write_spec, _ = setup_project
    runtime = Runtime(settings)
    try:
        await runtime.reconcile()
        previous = runtime.active["demo"]
        spec_data["models"]["Item"]["fields"]["extra"] = {"type": "boolean"}
        write_spec(spec_data)
        await runtime.reconcile()
        assert runtime.active["demo"] is previous
        assert "demo" in runtime.errors
    finally:
        await runtime.close()


async def test_additive_migration_and_failed_migration_rollback(setup_project, spec_data):
    settings, write_spec, _ = setup_project
    runtime = Runtime(settings)
    try:
        await runtime.reconcile()
        spec_data["models"]["Item"]["fields"]["extra"] = {"type": "boolean"}
        spec_data["database"].update(schemaVersion=2, migrations=[{
            "version": 2, "statements": ["ALTER TABLE Item ADD COLUMN extra BOOLEAN"],
        }])
        write_spec(spec_data)
        await runtime.reconcile()
        assert not runtime.errors
        previous = runtime.active["demo"]
        spec_data["database"].update(schemaVersion=3)
        spec_data["database"]["migrations"].append({"version": 3, "statements": [
            "ALTER TABLE Item ADD COLUMN unfinished BOOLEAN", "ALTER TABLE Missing ADD COLUMN failure BOOLEAN",
        ]})
        write_spec(spec_data)
        await runtime.reconcile()
        assert runtime.active["demo"] is previous
        import sqlalchemy as sa
        assert "unfinished" not in {c["name"] for c in sa.inspect(previous.storage.engine).get_columns("Item")}
    finally:
        await runtime.close()


async def test_github_fetches_spec_at_resolved_revision():
    from looprd.spec import Source
    reader = SourceReader("https://raw.githubusercontent.com/example/config/main/sources.yaml")
    sha = "a" * 40
    calls = []

    def respond(request):
        calls.append(str(request.url))
        if "/commits/" in str(request.url):
            assert request.headers["accept"] == "application/vnd.github.sha"
            return httpx.Response(200, text=sha)
        return httpx.Response(200, text="apiVersion: looprd/v1\ncomponent: demo\nrelease: '1'\napi: {version: v1}\nauth: {mode: public}")

    await reader.client.aclose()
    reader.client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    spec, revision = await reader.component(Source(id="demo", repository="https://github.com/example/demo.git"))
    assert revision == sha
    assert calls[-1] == f"https://api.github.com/repos/example/demo/contents/looprd.yaml?ref={sha}"
    await reader.close()


async def test_remote_404_and_invalid_document_are_not_removal(setup_project):
    settings, _, _ = setup_project
    runtime = Runtime(settings)
    await runtime.reconcile()
    old_reader = runtime.reader
    await old_reader.close()
    reader = SourceReader("https://example.test/sources.yaml")
    response = {"status": 404, "text": "missing"}

    def respond(request):
        return httpx.Response(response["status"], text=response["text"])

    await reader.client.aclose()
    reader.client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    runtime.reader = reader
    try:
        assert not await runtime.reconcile()
        assert "demo" in runtime.active
        response.update(status=200, text="apiVersion: looprd/v1\ncomponents: null")
        assert not await runtime.reconcile()
        assert "demo" in runtime.active
        response["text"] = "apiVersion: looprd/v1\ncomponents: []"
        assert await runtime.reconcile()
        assert not runtime.active
    finally:
        await runtime.close()


async def test_failed_checkpoint_preserves_active_dispatcher(setup_project, spec_data, monkeypatch):
    settings, write_spec, _ = setup_project
    runtime = Runtime(settings)
    try:
        await runtime.reconcile()
        previous = runtime.active["demo"]
        spec_data["release"] = "2"
        write_spec(spec_data)

        def fail(*args):
            raise OSError("simulated full disk")

        monkeypatch.setattr(runtime, "save", fail)
        assert not await runtime.reconcile()
        assert runtime.active["demo"] is previous
    finally:
        await runtime.close()

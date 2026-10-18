"""DESIGN.adoc / Purpose / Operator schedule and storage visibility."""

import asyncio
import json
import os
import time
from types import SimpleNamespace

from starlette.testclient import TestClient

from looprd.app import create_app
from looprd.storage_usage import measure
from test_http import ADMIN
from test_container_metrics import collector


def test_schedule_timers_and_authentication(setup_project):
    settings, _, _ = setup_project
    with TestClient(create_app(settings)) as client:
        for path in ("schedule", "storage"):
            assert client.get("/_looprd/" + path).status_code == 401
        response = client.get("/_looprd/schedule", headers=ADMIN)
        assert response.headers["cache-control"] == "no-store"
        rows = response.json()["items"]
        refresh = next(row for row in rows if row["name"] == "Repository refresh")
        assert time.time() < refresh["nextAt"] <= time.time() + settings.poll_seconds
        cleanup = next(row for row in rows if row["name"] == "Expired file cleanup")
        assert time.time() < cleanup["nextAt"] <= time.time() + 30
        assert "test-token" not in response.text and "source" not in response.text


def test_periodic_and_retry_schedule_uses_sql_without_payloads(setup_project, spec_data):
    settings, write, _ = setup_project
    settings.allow_python = True
    spec_data["jobs"] = {"refresh": {"intervalSeconds": 3600, "execution": "inline",
        "source": "async def handle(ctx): pass"}}
    write(spec_data)
    with TestClient(create_app(settings)) as client:
        generation = client.app.state.runtime.active["demo"]
        async def pause():
            generation.job_task.cancel()
            await asyncio.gather(generation.job_task, return_exceptions=True)
        client.portal.call(pause)
        store = generation.storage.jobs
        identity = store.enqueue("refresh", {"private": "never expose this payload"}, "private-owner", delay=120)
        with generation.storage.engine.begin() as connection:
            connection.execute(store.table.update().where(store.table.c.id == identity).values(attempts=1))
        response = client.get("/_looprd/schedule", headers=ADMIN)
        rows = [row for row in response.json()["items"] if row["component"] == "demo"]
        assert {row["kind"] for row in rows} == {"periodic job", "job retry"}
        assert all(row["state"] == "unavailable" for row in rows)
        assert next(row for row in rows if row["kind"] == "periodic job")["cadenceSeconds"] == 3600
        assert "private-owner" not in response.text and "never expose" not in response.text
        client.put("/_looprd/components/demo/availability", headers=ADMIN, json={"blocked": True})
        rows = client.get("/_looprd/schedule", headers=ADMIN).json()["items"]
        assert all(row["apiBlocked"] for row in rows if row["component"] == "demo")


def test_storage_categorization_bounds_and_private_paths(tmp_path):
    (tmp_path / "databases").mkdir()
    (tmp_path / "databases/demo.sqlite3").write_bytes(b"x" * 40)
    (tmp_path / "files").mkdir()
    (tmp_path / "files/private-filename").write_bytes(b"x" * 20)
    (tmp_path / "operations.sqlite3").write_bytes(b"x" * 10)
    report = measure(tmp_path, {"demo"})
    assert report["state"] == "complete" and report["measuredBytes"] == 70
    assert any(row["component"] == "demo" and row["bytes"] == 40 for row in report["categories"])
    assert "private-filename" not in json.dumps(report) and str(tmp_path) not in json.dumps(report)
    partial = measure(tmp_path, {"demo"}, max_entries=1)
    assert partial["state"] == "partial" and partial["scannedEntries"] == 1


def test_storage_does_not_follow_links_and_counts_hardlinks_once(tmp_path):
    root = tmp_path / "data"; root.mkdir()
    file = root / "one"; file.write_bytes(b"x" * 10)
    os.link(file, root / "two")
    outside = tmp_path / "outside"; outside.write_bytes(b"x" * 100)
    try:
        (root / "escape").symlink_to(outside)
    except OSError:
        pass  # Windows may not grant symlink privilege; hardlink case still runs.
    assert measure(root, set())["measuredBytes"] == 10


def test_storage_thresholds_are_based_on_whole_filesystem(tmp_path, monkeypatch):
    from looprd import storage_usage
    gib = 1024**3
    for used, free, level in [(79, 21*gib, "normal"), (80, 20*gib, "warning"),
                              (90, 10*gib, "critical"), (20, gib-1, "critical")]:
        monkeypatch.setattr(storage_usage.shutil, "disk_usage", lambda _: SimpleNamespace(
            total=100*gib, used=used*gib, free=free))
        result = measure(tmp_path, set())
        assert result["filesystem"]["level"] == level
        assert result["measuredBytes"] == 0


def test_storage_endpoint_cache_and_companion_measurements(setup_project):
    settings, _, _ = setup_project
    settings.container_metrics_file = settings.data.parent / "containers.json"
    settings.container_metrics_file.write_text(json.dumps({"version": 1, "collectedAt": time.time(),
        "memorySupported": False, "containers": [{"name": "looprd-redis", "state": "running"}],
        "storage": [{"name": "looprd-postgres-data", "state": "available", "allocatedBytes": 123456},
                    {"name": "looprd-redis-data", "state": "unavailable", "allocatedBytes": None}]}))
    with TestClient(create_app(settings)) as client:
        first = client.get("/_looprd/storage", headers=ADMIN)
        assert first.headers["cache-control"] == "no-store"
        report = first.json()
        assert report["filesystem"]["freeBytes"] > 0
        assert report["host"]["storage"][0]["allocatedBytes"] == 123456
        assert report["host"]["storage"][1]["allocatedBytes"] is None
        assert report["host"]["containers"][0]["name"] == "looprd-redis"
        assert client.get("/_looprd/storage", headers=ADMIN).json()["measuredAt"] == report["measuredAt"]
        assert "admin-key" not in first.text and str(settings.data) not in first.text


def test_host_storage_sampler_only_named_volumes_and_no_paths_exported(tmp_path):
    calls = []
    def runner(arguments):
        calls.append(arguments)
        if arguments[-1] == "looprd-redis-data":
            raise ValueError("not configured")
        return str(tmp_path)
    def disk(arguments, **kwargs):
        assert arguments == ["du", "-s", "-B1", "--", str(tmp_path)]
        assert kwargs["timeout"] == 3
        return SimpleNamespace(returncode=0, stdout="12345\tprivate-host-path\n")
    rows = collector.collect_storage(runner, disk)
    assert len(rows) == 4
    assert {call[-1] for call in calls} == {"looprd-data", "looprd-postgres-data", "looprd-redis-data", "looprd-ntfy-data"}
    assert next(row for row in rows if row["name"] == "looprd-redis-data")["allocatedBytes"] is None
    assert "private-host-path" not in str(rows) and str(tmp_path) not in str(rows)

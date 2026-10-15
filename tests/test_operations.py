"""Operational controls retain compatible defaults and recover work safely."""

import time

import sqlalchemy as sa
from starlette.testclient import TestClient

from looprd.app import create_app
from looprd.runtime_guard import RuntimeGuard
from test_http import ADMIN

AUTH = {"Authorization": "Bearer test-token"}


def test_process_deadline_jobs_recovery_and_private_metrics(setup_project, spec_data):
    settings, write, _ = setup_project
    settings.allow_python = True
    spec_data["handlers"] = [
        {"kind": "python", "path": "/process", "method": "POST", "execution": "process",
         "source": "async def handle(ctx): return {'proof': ctx.principal.id, 'value': ctx.body}"},
        {"kind": "python", "path": "/slow", "method": "GET", "execution": "process", "timeoutSeconds": 1,
         "source": "async def handle(ctx):\n while True: pass"}]
    spec_data["jobs"] = {"work": {"source": "async def handle(ctx): return ctx.body", "expose": True}}
    write(spec_data)
    with TestClient(create_app(settings)) as client:
        result = client.post("/looprd/demo/v1/process", json={"hidden": "input-secret"}, headers=AUTH)
        assert result.status_code == 200, result.text
        assert result.json()["proof"] == "bearer"
        assert client.get("/looprd/demo/v1/slow", headers=AUTH).status_code == 504
        identity = client.post("/looprd/demo/v1/_jobs/work", json={"payload": {"secret": "job-secret"}}, headers=AUTH).json()["id"]
        for _ in range(40):
            job = client.get("/looprd/demo/v1/_jobs/work/" + identity, headers=AUTH).json()
            if job["state"] == "complete":
                break
            time.sleep(0.1)
        assert job["state"] == "complete", job
        assert client.get("/_looprd/ops").status_code == 401
        time.sleep(0.2)
        metrics = client.get("/_looprd/ops", headers=ADMIN).json()
        assert metrics["metrics"]
        assert "input-secret" not in str(metrics) and "job-secret" not in str(metrics)


def test_job_stale_lease_and_retry_retains_payload(setup_project, spec_data):
    settings, write, _ = setup_project
    settings.allow_python = True
    spec_data["jobs"] = {"work": {"source": "async def handle(ctx): pass", "maxAttempts": 1}}
    write(spec_data)
    with TestClient(create_app(settings)) as client:
        store = client.app.state.runtime.active["demo"].storage.jobs
        identity = store.enqueue("work", {"value": 7}, "owner", delay=100)
        with store.storage.sessions.begin() as session:
            session.execute(store.table.update().where(store.table.c.id == identity).values(next=0))
        first = store.claim("first", 1)
        assert first["id"] == identity
        store.finish(first, "first", "SyntheticFailure")
        store.retry(identity)
        second = store.claim("second", 1)
        assert second["payload"] == {"value": 7}
        store.finish(first, "first")
        assert store.get("work", identity, "owner")["state"] == "running"
        store.finish(second, "second")
        assert store.get("work", identity, "owner")["state"] == "complete"


def test_native_quota_rate_limits_and_exclusive_runtime(setup_project, spec_data):
    settings, write, _ = setup_project
    spec_data["limits"] = {"maxRows": 1, "burstPerSecond": 3}
    write(spec_data)
    with TestClient(create_app(settings)) as client:
        assert client.post("/looprd/demo/v1/items", json={"name": "first"}, headers=AUTH).status_code == 201
        assert client.post("/looprd/demo/v1/items", json={"name": "second"}, headers=AUTH).status_code == 507
        assert client.get("/looprd/demo/v1/items", headers=AUTH).status_code == 200
        assert client.get("/looprd/demo/v1/items", headers=AUTH).status_code == 429
        guard = RuntimeGuard(settings.data)
        try:
            guard.acquire()
        except (ValueError, RuntimeError, OSError):
            pass
        else:
            guard.release()
            raise AssertionError("second worker acquired the live data directory")

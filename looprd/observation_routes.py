"""Read-only operator agenda and storage; no payloads, paths or automatic cleanup."""

import time

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from .storage_usage import StorageUsage
from .web import admin_access


def task(name, cadence, state="active", next_at=None, component=None, kind="maintenance", error=None):
    return {"name": name, "kind": kind, "component": component, "state": state,
        "cadenceSeconds": cadence, "nextAt": next_at, "error": error}


async def schedule(request):
    denied = admin_access(request)
    if denied is not None:
        return denied
    runtime = request.app.state.runtime
    monitor = runtime.monitor
    rows = [task("Repository refresh", runtime.settings.poll_seconds,
        "running" if monitor.checking else "waiting", monitor.next_poll_at)]
    files = request.app.state.files
    rows.append(task("Expired file cleanup", 30, files.sweep_state, files.next_sweep_at, error=files.sweep_error))
    rows.append(task("Telemetry flush", .1, "error" if request.app.state.operations.error else "active",
        error=request.app.state.operations.error))
    if runtime.discovery:
        rows.append(task("Discovery observation", None, "running" if runtime.discovery.task and not runtime.discovery.task.done() else "waiting"))
    truncated = False
    for component, generation in list(runtime.active.items())[:100]:
        blocked = component in runtime.availability.blocked
        if generation.storage and generation.storage.jobs:
            generation.active_requests += 1
            try:
                agenda = await run_in_threadpool(generation.storage.jobs.agenda)
                truncated |= agenda["truncated"]
                for row in agenda["items"]:
                    row["component"] = component
                    row["apiBlocked"] = blocked
                    if generation.job_error or not generation.job_task or generation.job_task.done():
                        row["state"] = "unavailable"
                    rows.append(row)
            except Exception:
                rows.append(task("Job schedule unavailable", 1, "unavailable", component=component))
            finally:
                generation.active_requests -= 1
        if generation.spec.realtime.delivery == "durable":
            rows.append(task("Durable event relay", 1, "error" if generation.relay_error else "active",
                component=component, error=generation.relay_error))
        if generation.spec.push:
            rows.append(task("Push delivery worker", 1, "blocked" if blocked else "error" if generation.push_error else "active",
                component=component, error=generation.push_error))
        if len(rows) >= 1000:
            truncated = True
            break
    truncated |= len(runtime.active) > 100 or len(rows) > 1000
    rows.sort(key=lambda row: (row["nextAt"] is None, row["nextAt"] or 0, row["component"] or "", row["name"]))
    return JSONResponse({"version": 1, "generatedAt": time.time(), "items": rows[:1000], "truncated": truncated},
        headers={"Cache-Control": "no-store"})


async def storage(request):
    denied = admin_access(request)
    if denied is not None:
        return denied
    runtime = request.app.state.runtime
    if not getattr(request.app.state, "storage_usage", None):
        request.app.state.storage_usage = StorageUsage()
    try:
        result = await request.app.state.storage_usage.read(runtime.settings, frozenset(runtime.active) | frozenset(runtime.sources))
        from .container_metrics import read
        host = await run_in_threadpool(read, runtime.settings)
        result["host"] = {"state": host["state"], "ageSeconds": host.get("ageSeconds"), "storage": host.get("storage"),
            "memorySupported": host.get("memorySupported"),
            "containers": [{key: row.get(key) for key in ("name", "state", "memoryBytes", "memoryLimitBytes")}
                for row in host.get("containers", [])]}
        return JSONResponse(result, headers={"Cache-Control": "no-store"})
    except Exception:
        return JSONResponse({"error": "storage measurement unavailable"}, status_code=503,
            headers={"Cache-Control": "no-store"})

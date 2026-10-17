"""Management-only observation status; no endpoint sends or approves candidates."""

import sqlite3

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from .web import admin_access


async def endpoint(request):
    denied = admin_access(request)
    if denied is not None:
        return denied
    observer = request.app.state.runtime.discovery
    headers = {"Cache-Control": "no-store"}
    if observer is None:
        enabled = request.app.state.runtime.settings.discovery_enabled
        return JSONResponse({"mode": "unavailable" if enabled else "disabled", "observations": []}, status_code=503 if enabled else 200, headers=headers)
    try:
        component = request.path_params.get("component")
        if component is not None:
            record = await run_in_threadpool(observer.store.get, component)
            return JSONResponse(record if record else {"error": "observation not found"}, status_code=200 if record else 404, headers=headers)
        return JSONResponse({"mode": "observe-only", "delivery": "disabled",
            "observations": await run_in_threadpool(observer.store.list)}, headers=headers)
    except (sqlite3.Error, ValueError):
        return JSONResponse({"error": "discovery state unavailable"}, status_code=503, headers=headers)

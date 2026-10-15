"""Administrative link lifecycle and capability-only JSON diagnostics."""

import sqlite3

from pydantic import Field, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from .contract import Strict, Name
from .diagnostics import snapshot
from .web import admin_access

HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff"}


class CreateLink(Strict):
    component: Name | None = None
    expiresInSeconds: int = Field(default=3600, ge=60, le=43200, strict=True)


async def management(request):
    denied = admin_access(request)
    if denied is not None:
        return denied
    runtime = request.app.state.runtime
    store = runtime.diagnostics
    try:
        if request.method == "DELETE":
            found = await run_in_threadpool(store.revoke, request.path_params["link_id"])
            return JSONResponse({"revoked": bool(found)}, status_code=200 if found else 404, headers=HEADERS)
        if request.method == "POST":
            chunks, size = [], 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > 1024:
                    return JSONResponse({"error": "body_too_large"}, status_code=413, headers=HEADERS)
                chunks.append(chunk)
            payload = CreateLink.model_validate_json(b"".join(chunks))
            known = set(runtime.active) | set(runtime.sources) | set(runtime.errors)
            if payload.component is not None and payload.component not in known:
                return JSONResponse({"error": "component_not_found"}, status_code=404, headers=HEADERS)
            result = await run_in_threadpool(store.create, payload.component, payload.expiresInSeconds)
            return JSONResponse(result, status_code=201, headers=HEADERS)
        return JSONResponse({"snapshot": await run_in_threadpool(snapshot, runtime),
                             "links": await run_in_threadpool(store.links)}, headers=HEADERS)
    except (ValidationError, ValueError):
        return JSONResponse({"error": "invalid_request_or_link_limit", "maxExpiresInSeconds": 43200}, status_code=422, headers=HEADERS)
    except sqlite3.Error:
        return JSONResponse({"error": "diagnostics_unavailable"}, status_code=503, headers=HEADERS)


async def diagnostic(request):
    runtime = request.app.state.runtime
    try:
        grant = await run_in_threadpool(runtime.diagnostics.authorize, request.path_params["token"])
        if grant is None:
            return JSONResponse({"error": "diagnostic_link_unavailable"}, status_code=404, headers=HEADERS)
        result = await run_in_threadpool(snapshot, runtime, grant["component"])
        result["expiresAt"] = grant["expires"]
        return JSONResponse(result, headers=HEADERS)
    except sqlite3.Error:
        return JSONResponse({"error": "diagnostics_unavailable"}, status_code=503, headers=HEADERS)

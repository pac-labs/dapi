"""Submit through component authentication; review exclusively through management."""

import sqlite3
from typing import Literal

from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect
from starlette.responses import JSONResponse, Response

from .bugreport_contract import BugReport, MAX_BODY
from .bugreports import LIMIT
from .contract import Strict

HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


class ReportError(Exception):
    def __init__(self, status, code):
        self.status, self.code = status, code


async def body(request, limit=MAX_BODY):
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise ReportError(415, "json_required")
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise ReportError(413, "report_too_large")
        chunks.append(chunk)
    return b"".join(chunks)


def error_response(error):
    if isinstance(error, ReportError):
        return JSONResponse({"error": error.code}, status_code=error.status,
                            headers={**HEADERS, **({"Retry-After": "60"} if error.status == 429 else {})})
    if isinstance(error, ValidationError):
        return JSONResponse({"error": "invalid_bug_report"}, status_code=422, headers=HEADERS)
    if isinstance(error, ClientDisconnect):
        return JSONResponse({"error": "report_interrupted"}, status_code=408, headers=HEADERS)
    return JSONResponse({"error": "bug_report_storage_unavailable"}, status_code=503, headers=HEADERS)


async def submit(request, generation):
    if request.method != "POST":
        return JSONResponse({"error": "method_not_allowed"}, status_code=405, headers={**HEADERS, "Allow": "POST"})
    store = request.app.state.runtime.bugreports
    try:
        if not store.permit(generation.spec.component):
            raise ReportError(429, "bug_report_rate_limited")
        report = BugReport.model_validate_json(await body(request))
        result = await run_in_threadpool(store.submit, generation.spec.component, report)
        return JSONResponse(result, status_code=202, headers=HEADERS)
    except (ReportError, ValidationError, ClientDisconnect, sqlite3.Error, OSError) as error:
        return error_response(error)


class Review(Strict):
    state: Literal["open", "resolved"]


async def management(request):
    from .web import admin_access
    denied = admin_access(request)
    if denied is not None:
        return denied
    store = request.app.state.runtime.bugreports
    identity = request.path_params.get("report_id")
    try:
        if identity is None:
            return JSONResponse({"apiVersion": "looprd-bugreport-inbox/v1", "limit": LIMIT,
                                 "reports": await run_in_threadpool(store.list)}, headers=HEADERS)
        if request.method == "DELETE":
            if not await run_in_threadpool(store.delete, identity):
                raise ReportError(404, "bug_report_not_found")
            return Response(status_code=204, headers=HEADERS)
        if request.method == "PATCH":
            change = Review.model_validate_json(await body(request, 1024))
            if not await run_in_threadpool(store.update, identity, change.state):
                raise ReportError(404, "bug_report_not_found")
        result = await run_in_threadpool(store.get, identity)
        if result is None:
            raise ReportError(404, "bug_report_not_found")
        return JSONResponse(result, headers=HEADERS)
    except (ReportError, ValidationError, ClientDisconnect, sqlite3.Error, OSError) as error:
        return error_response(error)

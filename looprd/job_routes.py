"""Optional owner-scoped job submission/status and management recovery."""

import asyncio

from pydantic import Field, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, Response

from .contract import Strict
from .outbox import EventError


class Enqueue(Strict):
    payload: object = None
    delaySeconds: int = Field(default=0, ge=0, le=604800, strict=True)


async def request_job(request, generation, path, body):
    parts = path.strip("/").split("/")
    definition = generation.spec.jobs.get(parts[1]) if len(parts) in (2, 3) else None
    if not definition or not definition.expose:
        raise EventError(404, "job endpoint not found")
    principal = request.state.principal
    if not generation.permits(principal, definition.scopes):
        raise EventError(403, "insufficient job permission")
    store = generation.storage.jobs
    if len(parts) == 2 and request.method == "POST":
        try:
            payload = Enqueue.model_validate(body)
        except ValidationError:
            raise EventError(422, "invalid job submission") from None
        identity = await run_in_threadpool(store.enqueue, parts[1], payload.payload, principal.id, payload.delaySeconds)
        return JSONResponse({"id": identity}, status_code=202, headers={"Cache-Control": "no-store"})
    if len(parts) == 3 and request.method == "GET":
        return JSONResponse(await run_in_threadpool(store.get, parts[1], parts[2], principal.id), headers={"Cache-Control": "no-store"})
    raise EventError(405, "unsupported job operation")


async def manage_jobs(request):
    from .web import admin_access
    denied = admin_access(request)
    if denied is not None:
        return denied
    generation = request.app.state.runtime.active.get(request.path_params["component"])
    if not generation or not generation.storage or not generation.storage.jobs:
        return JSONResponse({"error": "jobs not enabled"}, status_code=404)
    generation.active_requests += 1
    try:
        if request.method == "GET":
            return JSONResponse({"jobs": await run_in_threadpool(generation.storage.jobs.list)}, headers={"Cache-Control": "no-store"})
        await run_in_threadpool(generation.storage.jobs.retry, request.path_params["job_id"])
        return Response(status_code=204)
    except EventError as error:
        return JSONResponse({"error": error.message}, status_code=error.status)
    except Exception as error:
        request.app.state.runtime.report(generation.spec.component, "job management", error)
        return JSONResponse({"error": "job operation unavailable"}, status_code=503)
    finally:
        generation.active_requests -= 1

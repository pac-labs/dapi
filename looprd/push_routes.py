"""Caller-owned registration with proof of endpoint possession."""

from pydantic import Field, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from .contract import Strict
from .web import admin_access


class Registration(Strict):
    installationId: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    endpoint: str = Field(min_length=1, max_length=2048)


class Confirmation(Strict):
    challenge: str = Field(min_length=43, max_length=43, pattern=r"^[A-Za-z0-9_-]+$")


async def dispatch(request, generation, path, body):
    store, policy = generation.storage.push if generation.storage else None, generation.spec.push
    if store is None:
        return JSONResponse({"error": "push not enabled"}, status_code=404)
    if not generation.permits(request.state.principal, policy.scopes):
        return JSONResponse({"error": "insufficient push scope"}, status_code=403)
    principal = request.state.principal
    parts = path.strip("/").split("/")
    headers = {"Cache-Control": "no-store"}
    try:
        if parts == ["_push", "devices"]:
            if request.method == "GET":
                return JSONResponse({"devices": await run_in_threadpool(store.list, principal)}, headers=headers)
            if request.method == "POST":
                if not generation.settings.push_public_url:
                    return JSONResponse({"error": "push provider not configured"}, status_code=503, headers=headers)
                value = Registration.model_validate(body)
                result = await run_in_threadpool(store.register, principal, value.installationId, value.endpoint)
                return JSONResponse(result, status_code=200 if result["status"] == "active" else 202, headers=headers)
        if len(parts) == 3 and parts[:2] == ["_push", "devices"] and request.method == "DELETE":
            found = await run_in_threadpool(store.remove, principal, parts[2])
            return JSONResponse({"removed": found}, status_code=200 if found else 404, headers=headers)
        if len(parts) == 4 and parts[:2] == ["_push", "devices"] and parts[3] == "verify" and request.method == "POST":
            value = Confirmation.model_validate(body)
            return JSONResponse(await run_in_threadpool(store.verify, principal, parts[2], value.challenge), headers=headers)
    except (ValidationError, ValueError):
        return JSONResponse({"error": "invalid push registration or provider endpoint"}, status_code=422, headers=headers)
    return JSONResponse({"error": "push route or method not found"}, status_code=404, headers=headers)


async def management(request):
    denied = admin_access(request)
    if denied is not None:
        return denied
    generation = request.app.state.runtime.active.get(request.path_params["component"])
    if not generation or not generation.storage or not generation.storage.push:
        return JSONResponse({"error": "push not enabled"}, status_code=404)
    generation.active_requests += 1
    try:
        counts = await run_in_threadpool(generation.storage.push.status)
        return JSONResponse({**counts, "configured": bool(generation.settings.push_public_url), "error": generation.push_error},
                            headers={"Cache-Control": "no-store"})
    finally:
        generation.active_requests -= 1

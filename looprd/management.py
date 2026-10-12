"""Admin-only component key provisioning and revocation."""

import asyncio

from pydantic import Field, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, Response

from .spec import Scope, Strict
from .web import admin_access


class CreateKey(Strict):
    label: str = Field(min_length=1, max_length=100)
    scopes: list[Scope] = Field(min_length=1)
    expiresInSeconds: int | None = Field(default=2592000, ge=1, le=31536000, strict=True)


async def keys(request):
    denied = admin_access(request)
    if denied is not None:
        return denied
    runtime = request.app.state.runtime
    generation = runtime.active.get(request.path_params["component"])
    if generation is None:
        return JSONResponse({"error": "component not found"}, status_code=404)
    if generation.spec.auth.mode != "database":
        return JSONResponse({"error": "component does not use database authentication"}, status_code=409)
    generation.active_requests += 1
    try:
        async with asyncio.timeout(runtime.settings.request_seconds):
            store = generation.storage.keys
            if request.method == "GET":
                return JSONResponse({"keys": await run_in_threadpool(store.list)}, headers={"Cache-Control": "no-store"})
            if request.method == "DELETE":
                revoked = await run_in_threadpool(store.revoke, request.path_params["key_id"])
                return Response(status_code=204) if revoked else JSONResponse({"error": "key not found"}, status_code=404)
            chunks, size = [], 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > runtime.settings.max_body_bytes:
                    return JSONResponse({"error": "body exceeds limit"}, status_code=413)
                chunks.append(chunk)
            try:
                payload = CreateKey.model_validate_json(b"".join(chunks))
            except (ValidationError, ValueError):
                return JSONResponse({"error": "invalid key request"}, status_code=422)
            if len(set(payload.scopes)) != len(payload.scopes) or not set(payload.scopes) <= set(generation.spec.auth.scopes):
                return JSONResponse({"error": "scopes must be unique and declared by the component"}, status_code=422)
            result = await run_in_threadpool(store.create, payload.label, payload.scopes, payload.expiresInSeconds)
            return JSONResponse(result, status_code=201, headers={"Cache-Control": "no-store"})
    except TimeoutError:
        return JSONResponse({"error": "request deadline exceeded"}, status_code=504)
    except Exception as error:
        runtime.report(generation.spec.component, "key management", error)
        return JSONResponse({"error": "key management failed"}, status_code=500)
    finally:
        generation.active_requests -= 1

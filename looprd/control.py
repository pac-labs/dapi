"""Management-only traffic blocking without source or data changes."""

import asyncio

from pydantic import Field, ValidationError
from starlette.responses import JSONResponse

from .spec import Strict
from .web import admin_access


class SetAvailability(Strict):
    blocked: bool = Field(strict=True)


async def availability(request):
    denied = admin_access(request)
    if denied is not None:
        return denied
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > 1024:
            return JSONResponse({"error": "body exceeds limit"}, status_code=413)
        chunks.append(chunk)
    try:
        payload = SetAvailability.model_validate_json(b"".join(chunks))
    except (ValueError, ValidationError):
        return JSONResponse({"error": "expected a boolean blocked field"}, status_code=422)
    runtime = request.app.state.runtime
    try:
        async with asyncio.timeout(runtime.settings.request_seconds):
            await runtime.set_blocked(request.path_params["component"], payload.blocked)
    except KeyError:
        return JSONResponse({"error": "component not found"}, status_code=404)
    except (OSError, ValueError, TimeoutError):
        return JSONResponse({"error": "availability update failed; check management status"}, status_code=503)
    return JSONResponse(runtime.status(), headers={"Cache-Control": "no-store"})

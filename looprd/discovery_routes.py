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
        settings = request.app.state.runtime.settings
        enabled = settings.discovery_enabled or settings.discovery_delivery_enabled
        return JSONResponse({"mode": "unavailable" if enabled else "disabled", "observations": []}, status_code=503 if enabled else 200, headers=headers)
    try:
        delivery = observer.delivery
        delivery_state = None
        if delivery:
            delivery_state = {"state": delivery.state,
                "queue": await run_in_threadpool(delivery.outbox.summaries, request.app.state.runtime.settings.deployment_id)}
        component = request.path_params.get("component")
        if component is not None:
            record = await run_in_threadpool(observer.store.get, component)
            if record and delivery:
                record["deliveryStatus"] = next((row for row in delivery_state["queue"] if row["component"] == component), None)
            return JSONResponse(record if record else {"error": "observation not found"}, status_code=200 if record else 404, headers=headers)
        return JSONResponse({"mode": "observe-and-deliver" if delivery else "observe-only", "delivery": delivery_state or "disabled",
            "observations": await run_in_threadpool(observer.store.list)}, headers=headers)
    except (sqlite3.Error, ValueError):
        return JSONResponse({"error": "discovery state unavailable"}, status_code=503, headers=headers)

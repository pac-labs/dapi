"""Authorization-preserving HTTP replay and WebSocket SQL catch-up."""

from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from .outbox import EventError


def allowed(generation, resource, principal):
    ws = generation.spec.websocket
    return bool(ws and resource in ws.resources and generation.permits(principal, ws.permissions.get(resource, [])))


async def replay(request, generation, resource):
    if not generation.storage or not generation.storage.events:
        return JSONResponse({"error": "durable events not enabled"}, status_code=404)
    if not allowed(generation, resource, request.state.principal):
        return JSONResponse({"error": "insufficient subscription permission"}, status_code=403)
    try:
        limit = int(request.query_params.get("limit", "100"))
        if not 1 <= limit <= 100:
            raise ValueError()
        result = await run_in_threadpool(generation.storage.events.page, resource,
            request.state.principal, request.query_params.get("after"), limit)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})
    except ValueError:
        return JSONResponse({"error": "limit must be between 1 and 100"}, status_code=422)
    except EventError as error:
        return JSONResponse({"error": error.message, "resetRequired": error.status == 410}, status_code=error.status)


async def start_cursor(socket, generation, subscriber, principal, resource, message):
    try:
        page = await run_in_threadpool(generation.storage.events.page, resource, principal,
                                       message.get("after"), 100, "after" not in message)
    except EventError as error:
        await socket.send_json({"error": error.message, "resetRequired": error.status == 410})
        return False
    subscriber.resources.add(resource)
    await socket.send_json({"subscribed": resource, "cursor": page["startCursor"], "delivery": "durable"})
    for event in page["events"]:
        await socket.send_json(event)
    subscriber.cursors[resource] = page["cursor"]
    return True


async def catch_up(socket, generation, subscriber, principal):
    for resource, cursor in tuple(subscriber.cursors.items()):
        if not allowed(generation, resource, principal):
            continue
        try:
            page = await run_in_threadpool(generation.storage.events.page, resource, principal, cursor)
        except EventError as error:
            await socket.send_json({"error": error.message, "resetRequired": error.status == 410})
            await socket.close(code=1012)
            return False
        for event in page["events"]:
            await socket.send_json(event)
        subscriber.cursors[resource] = page["cursor"]
    return True

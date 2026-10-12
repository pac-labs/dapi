"""HTTP dispatch, authentication, limits, and WebSocket subscription lifecycle."""

import asyncio
import json
from contextlib import suppress

from starlette.responses import JSONResponse
from starlette.websockets import WebSocketDisconnect

from .openapi import document
from .admin import authorized as admin_authorized


def find_generation(runtime, scope):
    params = scope["path_params"]
    generation = runtime.active.get(params["component"])
    if generation and generation.spec.api.version == params["version"]:
        return generation
    return None


def admin_access(request):
    runtime = request.app.state.runtime
    try:
        valid = admin_authorized(runtime.settings, request.headers.get("authorization"))
    except (ValueError, OSError):
        return JSONResponse({"error": "management disabled: provision a management key"}, status_code=503)
    if not valid:
        return JSONResponse({"error": "unauthorized"}, status_code=401)


async def admin(request):
    denied = admin_access(request)
    if denied is not None:
        return denied
    runtime = request.app.state.runtime
    if request.method == "POST":
        success = await runtime.reconcile()
        return JSONResponse({"reconciled": success, **runtime.status()}, status_code=200 if success else 503,
                            headers={"Cache-Control": "no-store"})
    return JSONResponse(runtime.status(), headers={"Cache-Control": "no-store"})


async def health(request):
    return JSONResponse({"status": "ok", "service": "looprd"})


async def dispatch(request):
    runtime = request.app.state.runtime
    generation = find_generation(runtime, request.scope)
    if not generation:
        return JSONResponse({"error": "component not found"}, status_code=404)
    generation.active_requests += 1
    try:
        async with asyncio.timeout(runtime.settings.request_seconds):
            principal = await generation.authenticate(request.headers.get("authorization"))
            if principal is None:
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            request.state.principal = principal
            path = "/" + request.path_params.get("path", "").strip("/")
            if path == "/openapi.json" and request.method == "GET":
                return JSONResponse(document(generation))
            if path == "/_ws-ticket" and request.method == "POST" and generation.spec.websocket:
                ws = generation.spec.websocket
                if not any(generation.permits(principal, ws.permissions.get(r, [])) for r in ws.resources):
                    return JSONResponse({"error": "insufficient scope"}, status_code=403)
                return JSONResponse({"ticket": runtime.tickets.issue(generation.key, principal), "expiresIn": 30})
            body = None
            chunks, size = [], 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > runtime.settings.max_body_bytes:
                    return JSONResponse({"error": "body exceeds limit"}, status_code=413)
                chunks.append(chunk)
            if size:
                try:
                    body = json.loads(b"".join(chunks))
                except (ValueError, UnicodeError):
                    return JSONResponse({"error": "invalid JSON"}, status_code=400)
            return await generation.handle(request, path, body)
    except TimeoutError:
        return JSONResponse({"error": "request deadline exceeded"}, status_code=504)
    except Exception as error:
        runtime.report(generation.spec.component, "request", error)
        return JSONResponse({"error": "component request failed"}, status_code=500)
    finally:
        generation.active_requests -= 1


async def websocket(socket):
    runtime = socket.app.state.runtime
    generation = find_generation(runtime, socket.scope)
    path = "/" + socket.path_params.get("path", "").strip("/")
    if not generation or not generation.spec.websocket or path != generation.spec.websocket.path.rstrip("/"):
        await socket.close(code=1008)
        return
    origin = socket.headers.get("origin")
    if origin and origin not in runtime.settings.allowed_origins:
        await socket.close(code=1008)
        return
    ticket = socket.query_params.get("ticket", "")
    generation.active_requests += 1
    try:
        async with asyncio.timeout(runtime.settings.request_seconds):
            principal = await generation.authenticate(socket.headers.get("authorization"))
            if principal is None:
                grant = runtime.tickets.consume(ticket, generation.key)
                if grant and grant[2] is not None:
                    principal = await generation.refresh_identity(grant[2])
    except Exception as error:
        runtime.report(generation.spec.component, "websocket authentication", error)
        principal = None
    finally:
        generation.active_requests -= 1
    if principal is None or generation not in runtime.active.values():
        await socket.close(code=1008)
        return
    await socket.accept()
    generation.sockets.add(socket)
    subscriber = generation.bus.subscribe()
    try:
        await subscriptions(socket, generation, subscriber, principal)
    except (WebSocketDisconnect, RuntimeError, OSError):
        pass
    finally:
        generation.bus.subscribers.discard(subscriber)
        generation.sockets.discard(socket)


async def subscriptions(socket, generation, subscriber, principal):
    receive = asyncio.create_task(socket.receive_text())
    event = asyncio.create_task(subscriber.queue.get())
    try:
        while True:
            done, _ = await asyncio.wait({receive, event}, timeout=1, return_when=asyncio.FIRST_COMPLETED)
            try:
                async with asyncio.timeout(generation.settings.request_seconds):
                    principal = await generation.refresh_identity(principal)
            except Exception:
                principal = None
            if principal is None:
                await socket.close(code=1008)
                return
            if receive in done:
                content = receive.result()
                if len(content.encode()) > generation.settings.max_body_bytes:
                    await socket.close(code=1009)
                    return
                try:
                    message = json.loads(content)
                    resource = message.get("subscribe") if isinstance(message, dict) else None
                    if resource not in generation.spec.websocket.resources:
                        raise ValueError("unknown subscription")
                except (ValueError, TypeError):
                    await socket.send_json({"error": "invalid subscription"})
                else:
                    if generation.permits(principal, generation.spec.websocket.permissions.get(resource, [])):
                        subscriber.resources.add(resource)
                        await socket.send_json({"subscribed": resource})
                    else:
                        await socket.send_json({"error": "insufficient scope"})
                receive = asyncio.create_task(socket.receive_text())
            if event in done:
                value = event.result()
                if subscriber.overflow:
                    await socket.close(code=1013)
                    return
                if generation.permits(principal, generation.spec.websocket.permissions.get(value["resource"], [])):
                    await socket.send_json(value)
                event = asyncio.create_task(subscriber.queue.get())
    finally:
        receive.cancel()
        event.cancel()
        with suppress(asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
            await receive
        with suppress(asyncio.CancelledError):
            await event

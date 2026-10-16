"""HTTP dispatch, authentication, limits, and WebSocket subscription lifecycle."""

import asyncio
import json
from contextlib import suppress

from starlette.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.websockets import WebSocketDisconnect

from .openapi import document
from .admin import authorized as admin_authorized
from .replay import replay, start_cursor, catch_up
from .outbox import EventError
from .cors import allowed_origins


def find_generation(runtime, scope):
    if "looprd.generation" in scope:
        return scope["looprd.generation"]
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


async def capabilities(request):
    denied = admin_access(request)
    if denied is not None:
        return denied
    from .capabilities import CAPABILITIES
    return JSONResponse({"apiVersion": "looprd/v1", "capabilities": sorted(CAPABILITIES)},
                        headers={"Cache-Control": "no-store"})


async def dispatch(request):
    runtime = request.app.state.runtime
    if request.path_params["component"] in runtime.availability.blocked:
        return JSONResponse({"error": "component API blocked by operator"}, status_code=503,
                            headers={"Cache-Control": "no-store"})
    generation = find_generation(runtime, request.scope)
    if not generation:
        return JSONResponse({"error": "component not found"}, status_code=404)
    if generation.spec.limits.maxInFlight and generation.http_inflight >= generation.spec.limits.maxInFlight:
        return JSONResponse({"error": "component concurrency limit reached"}, status_code=429, headers={"Retry-After": "1"})
    generation.http_inflight += 1
    generation.active_requests += 1
    try:
        async with asyncio.timeout(runtime.settings.request_seconds):
            peer = request.client.host if request.client else "unknown"
            await generation.limiter.check(peer, authentication=True)
            principal = await generation.authenticate(request.headers.get("authorization"))
            if principal is None:
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            request.state.principal = principal
            await generation.limiter.check(peer if generation.spec.auth.mode == "public" else principal.id)
            path = "/" + request.path_params.get("path", "").strip("/")
            if path == "/_bugreports":
                from .bugreport_routes import submit
                return await submit(request, generation)
            if path == "/openapi.json" and request.method == "GET":
                return JSONResponse(document(generation))
            if path.startswith("/_events/") and request.method == "GET":
                return await replay(request, generation, path.removeprefix("/_events/"))
            if path.startswith("/_sync/") and request.method == "GET":
                from .sync import snapshot
                return await snapshot(request, generation, path.removeprefix("/_sync/"))
            if path == "/_services" and request.method == "GET" and generation.spec.dependencies:
                from .services import discover
                return JSONResponse({"services": discover(runtime, generation)}, headers={"Cache-Control": "no-store"})
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
            if path.startswith("/_jobs/"):
                from .job_routes import request_job
                return await request_job(request, generation, path, body)
            if path == "/_push" or path.startswith("/_push/"):
                from .push_routes import dispatch as push_dispatch
                return await push_dispatch(request, generation, path, body)
            return await generation.handle(request, path, body)
    except TimeoutError:
        return JSONResponse({"error": "request deadline exceeded"}, status_code=504)
    except EventError as error:
        return JSONResponse({"error": error.message}, status_code=error.status,
                            headers={"Retry-After": "60"} if error.status == 429 else {})
    except Exception as error:
        runtime.report(generation.spec.component, "request", error)
        return JSONResponse({"error": "component request failed"}, status_code=500)
    finally:
        generation.http_inflight -= 1
        generation.active_requests -= 1


async def websocket(socket):
    runtime = socket.app.state.runtime
    component = socket.path_params["component"]
    if component in runtime.availability.blocked:
        await socket.close(code=1008)
        return
    generation = find_generation(runtime, socket.scope)
    path = "/" + socket.path_params.get("path", "").strip("/")
    if not generation or not generation.spec.websocket or path != generation.spec.websocket.path.rstrip("/"):
        await socket.close(code=1008)
        return
    origin = socket.headers.get("origin")
    if origin and origin not in allowed_origins(runtime.settings, generation.spec):
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
    if principal is None or generation not in runtime.active.values() or component in runtime.availability.blocked:
        await socket.close(code=1008)
        return
    await socket.accept()
    limit = generation.spec.limits.maxWebSockets
    if limit and sum(owner == component for owner in runtime.sockets.values()) >= limit:
        await socket.close(code=1013)
        return
    generation.sockets.add(socket)
    runtime.sockets[socket] = component
    subscriber = generation.bus.subscribe()
    try:
        if component in runtime.availability.blocked:
            await socket.close(code=1008)
        else:
            await subscriptions(socket, generation, subscriber, principal)
    except (WebSocketDisconnect, RuntimeError, OSError):
        pass
    except EventError:
        await socket.close(code=1013)
    except Exception as error:
        runtime.report(component, "websocket", error)
        try:
            await socket.close(code=1011)
        except (RuntimeError, OSError):
            pass
    finally:
        generation.bus.subscribers.discard(subscriber)
        generation.sockets.discard(socket)
        runtime.sockets.pop(socket, None)


async def subscriptions(socket, generation, subscriber, principal):
    receive = asyncio.create_task(socket.receive_text())
    event = asyncio.create_task(subscriber.queue.get())
    try:
        while True:
            done, _ = await asyncio.wait({receive, event}, timeout=1, return_when=asyncio.FIRST_COMPLETED)
            if generation.spec.component in socket.app.state.runtime.availability.blocked:
                await socket.close(code=1008)
                return
            try:
                async with asyncio.timeout(generation.settings.request_seconds):
                    principal = await generation.refresh_identity(principal)
            except Exception:
                principal = None
            if principal is None:
                await socket.close(code=1008)
                return
            if receive in done:
                await generation.limiter.check("ws:" + principal.id)
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
                        if generation.storage and generation.storage.events:
                            await start_cursor(socket, generation, subscriber, principal, resource, message)
                        else:
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
                if (not subscriber.cursors or "eventId" not in value) and generation.permits(principal, generation.spec.websocket.permissions.get(value["resource"], [])):
                    allowed_event = not generation.storage or await run_in_threadpool(
                        generation.storage.policies.event, value["resource"], principal, value.get("_looprd_record", value.get("data")))
                    if allowed_event:
                        await socket.send_json({key: data for key, data in value.items() if key != "_looprd_record"})
                event = asyncio.create_task(subscriber.queue.get())
            if subscriber.cursors and not await catch_up(socket, generation, subscriber, principal):
                return
    finally:
        receive.cancel()
        event.cancel()
        with suppress(asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
            await receive
        with suppress(asyncio.CancelledError):
            await event

"""Exact public GET dispatch; never issues or authenticates an application credential."""

import json

from starlette.responses import JSONResponse, Response
from starlette.requests import Request
from starlette.datastructures import MutableHeaders

from .auth import Principal
from .component_routes import parameters
from .outbox import EventError

MAX_RESPONSE = 262144
HEADERS = {"Cache-Control": "no-store, max-age=0", "X-Content-Type-Options": "nosniff",
           "Access-Control-Allow-Origin": "*"}


def failure(message="anonymous read unavailable", status=503):
    return JSONResponse({"error": message}, status_code=status, headers=HEADERS)


def declared(generation, scope):
    path = "/" + parameters(scope).get("path", "")
    return next((handler for handler in generation.spec.handlers
                 if handler.access == "anonymous" and handler.path == path), None)


def private_method(generation, handler, method):
    return any(other.path == handler.path and other.method == method
               and other.access != "anonymous" for other in generation.spec.handlers)


def known_route(generation, method, path):
    """Only opting-in components gain pre-authentication unknown-route 404s."""
    if any(item.path == path and item.method == method for item in generation.spec.handlers):
        return True
    if any(path == item.path.rstrip("/") or path.startswith(item.path.rstrip("/") + "/")
           for item in generation.spec.resources.values()):
        return True
    return path in ("/openapi.json", "/_ws-ticket", "/_bugreports", "/_services") or any(
        path == root or path.startswith(root + "/") for root in ("/_jobs", "/_events", "/_sync", "/_push"))


def bounded_json(value):
    parts, total = [], 0
    encoder = json.JSONEncoder(ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    for chunk in encoder.iterencode(value):
        data = chunk.encode("utf-8")
        total += len(data)
        if total > MAX_RESPONSE:
            raise ValueError("anonymous response exceeds byte limit")
        parts.append(data)
    return Response(b"".join(parts), media_type="application/json", headers=HEADERS)


def preflight(request):
    method = request.headers.get("access-control-request-method")
    headers = request.headers.get("access-control-request-headers", "")
    names = [name.strip().lower() for name in headers.split(",") if name.strip()]
    if (method not in (None, "GET") or len(headers) > 1024 or len(names) > 16
            or any(name not in ("accept", "content-type", "authorization") for name in names)):
        return failure("unsupported anonymous read preflight", 400)
    return JSONResponse({"methods": ["GET"]}, headers={**HEADERS,
        "Access-Control-Allow-Methods": "GET", "Access-Control-Allow-Headers": "Accept, Content-Type, Authorization",
        "Access-Control-Max-Age": "0"})


async def execute(generation, request, handler):
    request.scope["headers"] = [(name, value) for name, value in request.scope.get("headers", [])
                                if name.lower() not in (b"authorization", b"cookie", b"proxy-authorization")]
    request.__dict__.pop("_headers", None)
    request.__dict__.pop("_cookies", None)
    request.state.principal = Principal("anonymous", "anonymous", frozenset(), kind="anonymous")
    try:
        await generation.limiter.check(request.client.host if request.client else "unknown")
    except EventError as error:
        return failure("anonymous read limit exceeded" if error.status == 429 else "anonymous read unavailable",
                       429 if error.status == 429 else 503)
    try:
        if generation.storage is None:
            raise ValueError("anonymous read storage unavailable")
        return await generation.handle(request, handler.path, None)
    except Exception as error:
        generation.runtime.record_failure(generation.spec.component, "anonymous read", error)
        return failure()


async def route(app, runtime, generation, handler, scope, receive, send):
    if generation.spec.component in runtime.availability.blocked:
        return await failure("component API blocked by operator")(scope, receive, send)
    if scope["method"] == "OPTIONS":
        return await preflight(Request(scope, receive))(scope, receive, send)
    if scope["method"] != "GET":
        return await failure("component route not found", 404)(scope, receive, send)
    scope["looprd.anonymous_handler"] = handler
    started = False

    async def public_send(message):
        nonlocal started
        if message["type"] == "http.response.start":
            message = {**message, "headers": list(message.get("headers", []))}
            headers = MutableHeaders(scope=message)
            for name in ("Access-Control-Allow-Credentials", "Set-Cookie"):
                if name in headers:
                    del headers[name]
            headers.update(HEADERS)
            headers["Content-Type"] = "application/json"
            started = True
        await send(message)

    try:
        await app(scope, receive, public_send)
    except Exception as error:
        if started:
            raise
        runtime.record_failure(generation.spec.component, "anonymous read", error)
        await failure()(scope, receive, public_send)

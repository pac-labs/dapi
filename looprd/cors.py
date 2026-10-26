"""Generation-scoped HTTP CORS and shared WebSocket origin policy."""

from starlette.middleware.cors import CORSMiddleware


def allowed_origins(settings, spec):
    origins = set(settings.allowed_origins)
    if spec.cors is not None:
        origins.update(spec.cors.allowedOrigins)
    if settings.origin_restrictions is not None:
        origins.intersection_update(settings.origin_restrictions)
    return sorted(origins)


class ComponentCorsMiddleware:
    def __init__(self, app, runtime):
        self.app, self.runtime = app, runtime

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        settings = self.runtime.settings
        origins = list(settings.allowed_origins)
        enabled = bool(origins)
        from .component_routes import resolve
        generation = resolve(self.runtime, scope)
        if generation:
            from .anonymous_read import declared, private_method, route
            handler = declared(generation, scope)
            method = scope["method"]
            if method == "OPTIONS":
                from starlette.datastructures import Headers
                method = Headers(scope=scope).get("access-control-request-method", "GET")
            if handler and not private_method(generation, handler, method):
                return await route(self.app, self.runtime, generation, handler, scope, receive, send)
        if generation:
            # Dispatch and response headers must refer to the same generation.
            origins = allowed_origins(settings, generation.spec)
            enabled = enabled or generation.spec.cors is not None or settings.origin_restrictions is not None
        if not enabled:
            return await self.app(scope, receive, send)
        middleware = CORSMiddleware(
            self.app, allow_origins=origins,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["Authorization", "Content-Type", "If-Match"], expose_headers=["ETag"],
        )
        await middleware(scope, receive, send)

"""ASGI application factory: one worker, many reloadable components."""

from contextlib import asynccontextmanager

from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.routing import Route, WebSocketRoute

from .config import Settings
from .runtime import Runtime
from .web import admin, dispatch, health, websocket, capabilities
from .management import keys
from .admin import announce, provision
from .panel import panel
from .control import availability
from .file_routes import files_endpoint
from .file_service import start_files
from .job_routes import manage_jobs
from .operations import Operations, OperationsMiddleware, endpoint as operations_endpoint


def create_app(settings=None, runtime=None):
    settings = settings or Settings.from_env()
    runtime = runtime or Runtime(settings)

    @asynccontextmanager
    async def lifespan(app):
        if settings.bootstrap_admin:
            announce(provision(settings))
        await runtime.start()
        try:
            app.state.operations = Operations(settings)
            app.state.files = await start_files(settings)
            try:
                yield
            finally:
                await app.state.files.close()
        finally:
            if getattr(app.state, "operations", None):
                await app.state.operations.close()
            await runtime.close()

    routes = [
        Route("/_looprd/health", health),
        Route("/_looprd/panel/", panel),
        Route("/_looprd/status", admin),
        Route("/_looprd/capabilities", capabilities),
        Route("/_looprd/ops", operations_endpoint),
        Route("/_looprd/reconcile", admin, methods=["POST"]),
        Route("/_looprd/files", files_endpoint, methods=["GET", "POST"]),
        Route("/_looprd/files/upload/{token}", files_endpoint, methods=["PUT"]),
        Route("/_looprd/files/download/{file_id}", files_endpoint, methods=["GET", "HEAD"]),
        Route("/_looprd/files/{file_id}", files_endpoint, methods=["DELETE"]),
        Route("/_looprd/components/{component}/availability", availability, methods=["PUT"]),
        Route("/_looprd/components/{component}/keys", keys, methods=["GET", "POST"]),
        Route("/_looprd/components/{component}/keys/{key_id}", keys, methods=["DELETE"]),
        Route("/_looprd/components/{component}/jobs", manage_jobs),
        Route("/_looprd/components/{component}/jobs/{job_id}/retry", manage_jobs, methods=["POST"]),
    ]
    for prefix in ("/looprd", "/dapi"):
        # /dapi remains a URL alias for projects using the original draft path.
        for suffix in ("", "/{path:path}"):
            path = prefix + "/{component}/{version}" + suffix
            routes.append(Route(path, dispatch, methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]))
            routes.append(WebSocketRoute(path, websocket))
    app = Starlette(routes=routes, lifespan=lifespan)
    app.state.runtime = runtime
    runtime.app = app
    app.add_middleware(OperationsMiddleware)
    if settings.allowed_origins:
        app.add_middleware(CORSMiddleware, allow_origins=list(settings.allowed_origins),
                           allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                           allow_headers=["Authorization", "Content-Type", "If-Match"],
                           expose_headers=["ETag"])
    return app

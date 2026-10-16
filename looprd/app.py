"""ASGI application factory: one worker, many reloadable components."""

from contextlib import asynccontextmanager

from starlette.applications import Starlette
from .cors import ComponentCorsMiddleware
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
from .diagnostic_routes import management as diagnostic_management, diagnostic
from .diagnostic_contract import endpoint as diagnostic_contract
from .bugreport_routes import management as bugreport_management
from .administration_routes import endpoint as data_administration
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
        Route("/_looprd/diagnostics", diagnostic_management, methods=["GET", "POST"]),
        Route("/_looprd/diagnostics.openapi.json", diagnostic_contract),
        Route("/_looprd/bugreports", bugreport_management),
        Route("/_looprd/data", data_administration),
        Route("/_looprd/data/{component}", data_administration),
        Route("/_looprd/data/{component}/audit", data_administration),
        Route("/_looprd/data/{component}/resources/{resource}", data_administration),
        Route("/_looprd/data/{component}/resources/{resource}/{record_id}", data_administration, methods=["GET", "PATCH"]),
        Route("/_looprd/data/{component}/resources/{resource}/{record_id}/actions/{action}", data_administration, methods=["POST"]),
        Route("/_looprd/bugreports/{report_id}", bugreport_management, methods=["GET", "PATCH", "DELETE"]),
        Route("/_looprd/diagnostics/{link_id}", diagnostic_management, methods=["DELETE"]),
        Route("/_looprd/diagnostic/{token}", diagnostic),
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
    app.add_middleware(ComponentCorsMiddleware, runtime=runtime)
    return app

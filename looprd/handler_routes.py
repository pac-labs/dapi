"""Trusted handler execution and typed contract validation."""

import asyncio
from types import SimpleNamespace
from starlette.responses import JSONResponse
from .shapes import validate as validate_shape

async def dispatch_handler(self, request, path, body):
    for handler in self.spec.handlers:
        if path == (handler.path.rstrip("/") or "/") and request.method == handler.method:
            if not self.permits(request.state.principal, handler.scopes):
                return JSONResponse({"error": "insufficient scope"}, status_code=403)
            if handler.contract:
                principal = request.state.principal
                if handler.contract.serviceOnly and principal.kind != "service":
                    return JSONResponse({"error": "service identity required"}, status_code=403)
                if handler.contract.allowedCallers and principal.subject not in handler.contract.allowedCallers:
                    return JSONResponse({"error": "service caller not permitted"}, status_code=403)
                try:
                    validate_shape(body, handler.contract.request)
                except ValueError:
                    return JSONResponse({"error": "request violates service contract"}, status_code=422)
            function = self.handlers.get((path, handler.method))
            result = handler.response
            if handler.kind == "python" and handler.execution == "process":
                from .process_runner import run
                async with self.runtime.worker_slots:
                    result = await run(self, handler.source, body, request.state.principal,
                        min(handler.timeoutSeconds or self.settings.request_seconds, self.settings.request_seconds),
                        handler.secrets, {"method": request.method, "path": handler.path, "query_params": dict(request.query_params)})
            elif function:
                context = SimpleNamespace(
                    request=request, body=body, specification=self.spec,
                    principal=request.state.principal,
                    session_factory=self.storage.sessions if self.storage else None,
                    publish=self.bus.publish,
                    transaction=self.storage.events.transaction if self.storage and self.storage.events else (self.storage.sessions.begin if self.storage else None),
                    emit=self.storage.events.emit if self.storage and self.storage.events else None,
                    call=self.call,
                    secret=self.settings.secret,
                )
                async with asyncio.timeout(min(handler.timeoutSeconds or self.settings.request_seconds, self.settings.request_seconds)):
                    result = await function(context)
            if handler.contract:
                try:
                    validate_shape(result, handler.contract.response)
                except ValueError:
                    return JSONResponse({"error": "response violates service contract"}, status_code=500)
            return JSONResponse(result, status_code=handler.status)
    return None

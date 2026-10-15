"""A compiled generation, declarative CRUD, and optional inline extensions."""

import hashlib
import asyncio
import ast
import inspect

from starlette.concurrency import run_in_threadpool

from .database import Storage
from .auth import Principal
from .status import timestamp
from .outbox_relay import relay
from .verification import Verification
from .limits import RateLimiter


from .crud import NativeCRUD


class Generation(NativeCRUD):
    def __init__(self, spec, settings, bus):
        self.spec, self.settings, self.bus = spec, settings, bus
        python = any(handler.kind == "python" for handler in spec.handlers) or spec.auth.mode == "verified" or bool(spec.jobs)
        if python and (not settings.allow_python or settings.trusted_components is not None and spec.component not in settings.trusted_components):
            raise ValueError("Python execution requires host trust and LOOPRD_ALLOW_PYTHON=true")
        self.loaded_at = timestamp()
        self.digest = hashlib.sha256(spec.model_dump_json().encode()).hexdigest()
        self.key = f"{spec.component}:{self.digest}"
        self.prefix = f"/looprd/{spec.component}/{spec.api.version}"
        self.token = settings.secret(spec.auth.secretRef) if spec.auth.mode == "bearer" else None
        self.active_requests = 0
        self.sockets = set()
        self.storage = None
        self.handlers = {}
        self.relay_task = None
        self.job_task = None
        self.job_error = None
        self.relay_error = None
        self.verifier = Verification(self) if spec.auth.mode == "verified" else None
        self.runtime = None
        self.http_inflight = 0
        self.limiter = RateLimiter(spec, settings)
        for handler in spec.handlers:
            if handler.kind == "python":
                if not settings.allow_python:
                    raise ValueError("inline Python requires LOOPRD_ALLOW_PYTHON=true")
                if handler.execution == "process":
                    tree = ast.parse(handler.source)
                    if not any(isinstance(node, ast.AsyncFunctionDef) and node.name == "handle" for node in tree.body):
                        raise ValueError("process handler must define async def handle(ctx)")
                    continue
                namespace = {"__name__": f"looprd_component_{spec.component}_{self.digest}"}
                exec(compile(handler.source, "<looprd-inline-handler>", "exec"), namespace)
                function = namespace.get("handle")
                if not inspect.iscoroutinefunction(function):
                    raise ValueError("inline handler must define async def handle(ctx)")
                self.handlers[(handler.path.rstrip("/") or "/", handler.method)] = function
        for job in spec.jobs.values():
            if not any(isinstance(node, ast.AsyncFunctionDef) and node.name == "handle" for node in ast.parse(job.source).body):
                raise ValueError("job must define async def handle(ctx)")
        if spec.database:
            self.storage = Storage(spec, settings)
            self.storage.initialize()

    async def authenticate(self, authorization):
        import hmac
        if self.verifier:
            return await self.verifier.authenticate(authorization)
        if self.spec.auth.mode == "database":
            return await run_in_threadpool(self.storage.keys.authenticate, authorization)
        if self.token is None or hmac.compare_digest((authorization or "").encode(), f"Bearer {self.token}".encode()):
            return Principal(self.spec.auth.mode, self.spec.auth.mode, frozenset(), kind=self.spec.auth.mode)
        return None

    async def refresh_identity(self, principal):
        if self.verifier:
            fresh = await self.verifier.authenticate(principal.credential)
            return fresh if fresh and fresh.id == principal.id and fresh.session_id == principal.session_id else None
        if self.spec.auth.mode == "database":
            return await run_in_threadpool(self.storage.keys.identity, principal.key_id or principal.id)
        return principal

    def permits(self, principal, required):
        return self.spec.auth.mode not in ("database", "verified") or principal.permits(required)

    async def handle(self, request, path, body):
        from .handler_routes import dispatch_handler
        from .resource_routes import dispatch_resource
        result = await dispatch_handler(self, request, path, body)
        return result if result is not None else await dispatch_resource(self, request, path, body)

    def close(self):
        if self.storage:
            self.storage.close()

    async def call(self, component, contract, payload=None):
        from .services import call
        return await call(self.runtime, self, component, contract, payload)

    def start(self):
        if self.storage and self.storage.events and self.relay_task is None:
            self.relay_task = asyncio.create_task(relay(self))
        if self.storage and self.storage.jobs and self.job_task is None:
            from .job_worker import worker
            self.job_task = asyncio.create_task(worker(self))

    async def shutdown(self):
        if self.job_task:
            self.job_task.cancel()
            await asyncio.gather(self.job_task, return_exceptions=True)
        if self.relay_task:
            self.relay_task.cancel()
            await asyncio.gather(self.relay_task, return_exceptions=True)
        await self.limiter.close()
        await run_in_threadpool(self.close)

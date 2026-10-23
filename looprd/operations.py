"""Local bounded aggregates and metadata audit, with no request content retention."""

import asyncio
import time

import sqlalchemy as sa
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse


class Operations:
    def __init__(self, settings):
        self.engine = sa.create_engine(sa.URL.create("sqlite", database=str(settings.data / "operations.sqlite3")),
            connect_args={"check_same_thread": False, "timeout": 2})
        metadata = sa.MetaData()
        self.metrics = sa.Table("metrics_v1", metadata,
            sa.Column("minute", sa.Integer, primary_key=True), sa.Column("component", sa.String(48), primary_key=True),
            sa.Column("requests", sa.Integer), sa.Column("errors", sa.Integer), sa.Column("milliseconds", sa.Float))
        self.audit = sa.Table("audit_v1", metadata, sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("at", sa.Float), sa.Column("action", sa.String(32)), sa.Column("component", sa.String(48)),
            sa.Column("status", sa.Integer))
        metadata.create_all(self.engine)
        self.queue = asyncio.Queue(maxsize=512)
        self.dropped = 0
        self.error = None
        self.task = asyncio.create_task(self.worker())

    def record(self, component, status, milliseconds, action=None):
        try:
            self.queue.put_nowait((time.time(), component, status, milliseconds, action))
        except asyncio.QueueFull:
            self.dropped += 1

    def flush(self, records):
        with self.engine.begin() as connection:
            for at, component, status, milliseconds, action in records:
                minute = int(at // 60)
                condition = sa.and_(self.metrics.c.minute == minute, self.metrics.c.component == component)
                changed = connection.execute(self.metrics.update().where(condition).values(
                    requests=self.metrics.c.requests + 1, errors=self.metrics.c.errors + int(status >= 500),
                    milliseconds=self.metrics.c.milliseconds + milliseconds))
                if not changed.rowcount:
                    connection.execute(self.metrics.insert().values(minute=minute, component=component,
                        requests=1, errors=int(status >= 500), milliseconds=milliseconds))
                if action:
                    connection.execute(self.audit.insert().values(at=at, action=action, component=component, status=status))
            connection.execute(self.metrics.delete().where(self.metrics.c.minute < int(time.time() // 60) - 10080))
            oldest = connection.execute(sa.select(self.metrics.c.minute).order_by(self.metrics.c.minute.desc()).offset(99999).limit(1)).scalar()
            if oldest:
                connection.execute(self.metrics.delete().where(self.metrics.c.minute <= oldest))
            cutoff = connection.execute(sa.select(self.audit.c.id).order_by(self.audit.c.id.desc()).offset(9999).limit(1)).scalar()
            if cutoff:
                connection.execute(self.audit.delete().where(self.audit.c.id < cutoff))
            connection.execute(self.audit.delete().where(self.audit.c.at < time.time() - 604800))

    async def worker(self):
        while True:
            records = [await self.queue.get()]
            while not self.queue.empty() and len(records) < 100:
                records.append(self.queue.get_nowait())
            try:
                await run_in_threadpool(self.flush, records)
                self.error = None
            except Exception as error:
                self.error = type(error).__name__
                self.dropped += len(records)
            await asyncio.sleep(0.1)

    def snapshot(self):
        with self.engine.connect() as connection:
            metrics = [dict(row) for row in connection.execute(sa.select(self.metrics)
                .order_by(self.metrics.c.minute.desc(), self.metrics.c.component).limit(1000)).mappings()]
            audit = [dict(row) for row in connection.execute(sa.select(self.audit)
                .order_by(self.audit.c.id.desc()).limit(100)).mappings()]
        return {"metrics": metrics, "audit": audit, "dropped": self.dropped, "error": self.error}

    async def close(self):
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)
        records = []
        while not self.queue.empty():
            records.append(self.queue.get_nowait())
        if records:
            try:
                await run_in_threadpool(self.flush, records)
            except Exception:
                pass
        await run_in_threadpool(self.engine.dispose)


class OperationsMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        started, status = time.monotonic(), 500
        async def capture(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)
        try:
            await self.app(scope, receive, capture)
        finally:
            app = scope.get("app")
            operations = getattr(app.state, "operations", None) if app else None
            if operations:
                from .component_routes import parameters
                params = parameters(scope)
                runtime = app.state.runtime
                component = params.get("component")
                component = component if component in runtime.active else "runtime"
                # Only static operation categories, never paths, tokens, IDs, names or bodies.
                action = None
                if scope["method"] in ("POST", "PUT", "DELETE") and scope["path"].startswith("/_looprd/"):
                    action = "management-write" if status != 401 else "management-denied"
                operations.record(component, status, (time.monotonic() - started) * 1000, action)


async def endpoint(request):
    from .web import admin_access
    denied = admin_access(request)
    if denied is not None:
        return denied
    operations = request.app.state.operations
    try:
        result = await run_in_threadpool(operations.snapshot)
    except Exception:
        return JSONResponse({"error": "operations storage unavailable"}, status_code=503)
    runtime = request.app.state.runtime
    from .container_metrics import read
    result["containers"] = await run_in_threadpool(read, runtime.settings)
    result["components"] = {name: {"httpInFlight": generation.http_inflight,
        "webSockets": len(generation.sockets), "rateLimitError": generation.limiter.error,
        "jobError": generation.job_error, "relayError": generation.relay_error,
        "pushEnabled": generation.spec.push is not None, "pushError": generation.push_error}
        for name, generation in runtime.active.items()}
    return JSONResponse(result, headers={"Cache-Control": "no-store"})

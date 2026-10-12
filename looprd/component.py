"""A compiled generation, declarative CRUD, and optional inline extensions."""

import hashlib
import inspect
import json
from types import SimpleNamespace

import sqlalchemy as sa
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, Response

from .database import Storage
from .auth import Principal
from .status import timestamp


class Generation:
    def __init__(self, spec, settings, bus):
        self.spec, self.settings, self.bus = spec, settings, bus
        self.loaded_at = timestamp()
        self.digest = hashlib.sha256(spec.model_dump_json().encode()).hexdigest()
        self.key = f"{spec.component}:{self.digest}"
        self.prefix = f"/looprd/{spec.component}/{spec.api.version}"
        self.token = settings.secret(spec.auth.secretRef) if spec.auth.mode == "bearer" else None
        self.active_requests = 0
        self.sockets = set()
        self.storage = None
        self.handlers = {}
        for handler in spec.handlers:
            if handler.kind == "python":
                if not settings.allow_python:
                    raise ValueError("inline Python requires LOOPRD_ALLOW_PYTHON=true")
                namespace = {"__name__": f"looprd_component_{spec.component}_{self.digest}"}
                exec(compile(handler.source, "<looprd-inline-handler>", "exec"), namespace)
                function = namespace.get("handle")
                if not inspect.iscoroutinefunction(function):
                    raise ValueError("inline handler must define async def handle(ctx)")
                self.handlers[(handler.path.rstrip("/") or "/", handler.method)] = function
        if spec.database:
            self.storage = Storage(spec, settings)
            self.storage.initialize()

    async def authenticate(self, authorization):
        import hmac
        if self.spec.auth.mode == "database":
            return await run_in_threadpool(self.storage.keys.authenticate, authorization)
        if self.token is None or hmac.compare_digest((authorization or "").encode(), f"Bearer {self.token}".encode()):
            return Principal(self.spec.auth.mode, self.spec.auth.mode, frozenset())
        return None

    async def refresh_identity(self, principal):
        if self.spec.auth.mode == "database":
            return await run_in_threadpool(self.storage.keys.identity, principal.id)
        return principal

    def permits(self, principal, required):
        return self.spec.auth.mode != "database" or principal.permits(required)

    async def handle(self, request, path, body):
        for handler in self.spec.handlers:
            if path == (handler.path.rstrip("/") or "/") and request.method == handler.method:
                if not self.permits(request.state.principal, handler.scopes):
                    return JSONResponse({"error": "insufficient scope"}, status_code=403)
                function = self.handlers.get((path, handler.method))
                result = handler.response
                if function:
                    context = SimpleNamespace(
                        request=request, body=body, specification=self.spec,
                        principal=request.state.principal,
                        session_factory=self.storage.sessions if self.storage else None,
                        publish=self.bus.publish,
                        secret=self.settings.secret,
                    )
                    result = await function(context)
                return JSONResponse(result, status_code=handler.status)
        for name, resource in self.spec.resources.items():
            base = resource.path.rstrip("/") or "/"
            item = path[len(base) + 1:] if path.startswith(base + "/") else None
            operation = None
            record_id = None
            if path == base:
                operation = {"GET": "list", "POST": "create"}.get(request.method)
            elif item and item.isdecimal():
                operation = {"GET": "get", "PATCH": "update", "DELETE": "delete"}.get(request.method)
                record_id = int(item)
            else:
                continue
            if operation not in resource.operations:
                return JSONResponse({"error": "method not allowed"}, status_code=405)
            if not self.permits(request.state.principal, resource.permissions.get(operation, [])):
                return JSONResponse({"error": "insufficient scope"}, status_code=403)
            try:
                limit = min(100, max(1, int(request.query_params.get("limit", "50"))))
                offset = max(0, int(request.query_params.get("offset", "0")))
                result, status, event = await run_in_threadpool(
                    self._crud, name, operation, record_id, body, limit, offset,
                )
            except (ValueError, ValidationError):
                return JSONResponse({"error": "invalid request fields or pagination"}, status_code=422)
            except sa.exc.IntegrityError:
                return JSONResponse({"error": "record constraint conflict"}, status_code=409)
            if event:
                self.bus.publish(event)
            if status == 204:
                return Response(status_code=204)
            return JSONResponse(json_safe(result), status_code=status)
        return JSONResponse({"error": "route not found"}, status_code=404)

    def _crud(self, name, operation, record_id, body, limit, offset):
        resource = self.spec.resources[name]
        table = self.storage.tables[resource.model]
        pk = next(iter(table.primary_key.columns))
        event = None
        with self.storage.sessions.begin() as session:
            if operation == "list":
                rows = session.execute(sa.select(table).order_by(pk).limit(limit).offset(offset)).mappings()
                return [dict(row) for row in rows], 200, None
            if operation == "create":
                validator = self.storage.inputs[resource.model][0]
                values = validator.model_validate_json(json.dumps(body)).model_dump()
                inserted = session.execute(table.insert().values(**values))
                record_id = inserted.inserted_primary_key[0]
                status, action = 201, "created"
            else:
                row = session.execute(sa.select(table).where(pk == record_id)).mappings().first()
                if row is None:
                    return {"error": "record not found"}, 404, None
                if operation == "get":
                    return dict(row), 200, None
                if operation == "delete":
                    session.execute(table.delete().where(pk == record_id))
                    status, action = 204, "deleted"
                else:
                    validator = self.storage.inputs[resource.model][1]
                    values = validator.model_validate_json(json.dumps(body)).model_dump(exclude_unset=True)
                    if values:
                        session.execute(table.update().where(pk == record_id).values(**values))
                    status, action = 200, "updated"
            result = None
            if action != "deleted":
                result = dict(session.execute(sa.select(table).where(pk == record_id)).mappings().one())
            event = json_safe({"component": self.spec.component, "resource": name,
                               "action": action, "id": record_id, "data": result})
        return result, status, event

    def close(self):
        if self.storage:
            self.storage.close()


def json_safe(value):
    return json.loads(json.dumps(value, default=lambda obj: obj.isoformat()))

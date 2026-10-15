"""Native HTTP resource routing and conditional revision transport."""

import sqlalchemy as sa
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, Response
from .outbox import EventError
from .crud import json_safe

async def dispatch_resource(self, request, path, body):
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
            revision = None
            if resource.revisionField and operation in ("update", "delete"):
                header = request.headers.get("if-match")
                if header is None:
                    raise EventError(428, "If-Match revision is required")
                value = header.removeprefix('"').removesuffix('"')
                if not value.isdecimal() or len(value) > 19 or not 1 <= int(value) < 2**63 - 1:
                    raise EventError(412, "invalid revision")
                revision = int(value)
            result, status, event = await run_in_threadpool(
                self._crud, name, operation, record_id, body, limit, offset, request.state.principal,
                dict(request.query_params), revision,
            )
        except (ValueError, ValidationError):
            return JSONResponse({"error": "invalid request fields or pagination"}, status_code=422)
        except sa.exc.IntegrityError:
            return JSONResponse({"error": "record constraint conflict"}, status_code=409)
        except EventError as error:
            return JSONResponse({"error": error.message}, status_code=error.status)
        if event:
            self.bus.publish(event)
        if status == 204:
            return Response(status_code=204)
        headers = {"ETag": '"' + str(result[resource.revisionField]) + '"'} if resource.revisionField and isinstance(result, dict) and resource.revisionField in result else {}
        return JSONResponse(json_safe(result), status_code=status, headers=headers)
    return JSONResponse({"error": "route not found"}, status_code=404)


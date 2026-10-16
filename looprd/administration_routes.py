"""Operator-only data plane; component credentials never grant administration."""

import asyncio
import re

from pydantic import ConfigDict, Field, ValidationError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from .administration import AdministrationError
from .bugreport_routes import body, ReportError
from .contract import Strict
from .outbox import EventError
from .web import admin_access

HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


class OperatorWrite(Strict):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    generation: str = Field(pattern=r"^[0-9a-f]{64}$")
    etag: str = Field(pattern=r"^[0-9a-f]{64}$")
    operationId: str = Field(pattern=r"^[a-zA-Z0-9_-]{8,64}$")
    reason: str = Field(min_length=3, max_length=500, pattern=r"\S.*\S")
    values: dict = Field(max_length=32)


def current(runtime, component):
    generation = runtime.active.get(component)
    if generation is None:
        raise AdministrationError(404, "component_not_active")
    if generation.administration is None:
        raise AdministrationError(403, "data_administration_not_enabled_in_specification")
    return generation


def coordinate(value, maximum=1000000):
    if not re.fullmatch(r"[0-9]{1,19}", str(value)) or not 0 <= int(value) <= maximum:
        raise AdministrationError(422, "invalid_record_or_page")
    return int(value)


async def execute(runtime, component, resource, identity, payload, action):
    # Hold the generation/policy lock until the transaction and notifications finish.
    async with runtime.lock:
        generation = current(runtime, component)
        generation.active_requests += 1

        async def transaction():
            receipt, events = await asyncio.to_thread(generation.administration.write, resource, identity, payload, action)
            for event in events:
                try:
                    generation.bus.publish(event)
                except Exception as error:
                    runtime.record_failure(component, "management-event", error)
                    receipt["notificationWarning"] = "change_committed_refresh_client_records"
            return receipt

        work = asyncio.create_task(transaction())
        try:
            try:
                return await asyncio.shield(work)
            except asyncio.CancelledError:
                await work
                raise
        finally:
            generation.active_requests -= 1


async def endpoint(request):
    denied = admin_access(request)
    if denied is not None:
        return denied
    runtime = request.app.state.runtime
    component = request.path_params.get("component")
    try:
        if component is None:
            return JSONResponse({"components": {name: {"enabled": value.administration is not None,
                "blocked": name in runtime.availability.blocked, "release": value.spec.release}
                for name, value in runtime.active.items()}}, headers=HEADERS)
        generation = current(runtime, component)
        resource = request.path_params.get("resource")
        identity = request.path_params.get("record_id")
        if identity is not None:
            identity = coordinate(identity, 2**63 - 1)
            if identity == 0:
                raise AdministrationError(422, "invalid_record_id")
        if request.method in ("PATCH", "POST"):
            payload = OperatorWrite.model_validate_json(await body(request, 16384))
            result = await execute(runtime, component, resource, identity, payload, request.path_params.get("action"))
            return JSONResponse(result, headers=HEADERS)
        generation.active_requests += 1
        try:
            if resource is not None:
                offset = coordinate(request.query_params.get("offset", "0"))
                query = request.query_params.get("q", "")
                if len(query) > 100:
                    raise AdministrationError(422, "search_too_long")
                result = await run_in_threadpool(generation.administration.read, resource, identity, offset, query)
            elif request.url.path.endswith("/audit"):
                offset = coordinate(request.query_params.get("offset", "0"))
                def audit():
                    with generation.storage.sessions() as session:
                        return generation.storage.operator_audit.list(session, offset)
                result = await run_in_threadpool(audit)
            else:
                result = generation.administration.catalog()
            return JSONResponse(result, headers=HEADERS)
        finally:
            generation.active_requests -= 1
    except (AdministrationError, ReportError) as error:
        return JSONResponse({"error": error.code}, status_code=error.status, headers=HEADERS)
    except ValidationError:
        return JSONResponse({"error": "invalid_operator_input"}, status_code=422, headers=HEADERS)
    except ValueError:
        return JSONResponse({"error": "invalid_operator_values"}, status_code=422, headers=HEADERS)
    except IntegrityError:
        return JSONResponse({"error": "record_constraint_conflict"}, status_code=409, headers=HEADERS)
    except EventError as error:
        return JSONResponse({"error": "event_transaction_unavailable"}, status_code=error.status, headers=HEADERS)
    except (SQLAlchemyError, OSError):
        return JSONResponse({"error": "data_administration_storage_unavailable"}, status_code=503, headers=HEADERS)
    except Exception as error:
        runtime.record_failure(component, "management-action", error)
        return JSONResponse({"error": "management_action_failed_transaction_rolled_back"}, status_code=500, headers=HEADERS)

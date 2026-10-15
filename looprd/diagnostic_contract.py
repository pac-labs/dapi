"""Machine-readable diagnostic response schema and endpoint discovery."""

from typing import Literal

from pydantic import Field
from starlette.responses import JSONResponse

from .contract import Strict


class Scope(Strict):
    component: str | None
    readOnly: Literal[True]
    applicationData: Literal[False]


class Component(Strict):
    id: str
    loaded: bool
    blocked: bool
    currentFailure: str | None
    lastWorkingRetained: bool
    release: str | None = None
    schemaVersion: int | None = None
    backend: str | None = None
    apiVersion: str | None = None
    jobFailure: str | None = None
    eventRelayFailure: str | None = None


class Failure(Strict):
    id: str
    component: str | None
    stage: str
    lastSeenAt: float = Field(description="Unix seconds; repeat occurrences update this time.")
    code: str
    message: str
    action: str
    exceptionType: str
    details: dict


class Snapshot(Strict):
    apiVersion: Literal["looprd-diagnostics/v1"]
    kind: Literal["DiagnosticSnapshot"]
    contractPath: Literal["/_looprd/diagnostics.openapi.json"]
    generatedAt: float
    scope: Scope
    sourcesFailed: bool | None
    components: list[Component]
    failures: list[Failure]
    historyRetentionSeconds: Literal[604800]
    expiresAt: float | None = None


def document():
    schema = Snapshot.model_json_schema(ref_template="#/components/schemas/{model}")
    definitions = schema.pop("$defs", {})
    definitions["DiagnosticSnapshot"] = schema
    return {"openapi": "3.1.0", "info": {"title": "Looprd diagnostic reader", "version": "1"},
            "paths": {"/_looprd/diagnostic/{token}": {"get": {
                "operationId": "readDiagnostics", "summary": "Read sanitized diagnostics using an expiring capability URL",
                "parameters": [{"name": "token", "in": "path", "required": True, "schema": {"type": "string"}}],
                "responses": {"200": {"description": "Read-only operational metadata; no application records",
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/DiagnosticSnapshot"}}}},
                    "404": {"description": "Unknown, revoked or expired capability"},
                    "503": {"description": "Diagnostic storage unavailable"}}}}},
            "components": {"schemas": definitions}}


async def endpoint(request):
    return JSONResponse(document())

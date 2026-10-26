"""HTTP documentation generated from the same active specification as the routes."""


def document(generation):
    spec = generation.spec
    paths, schemas = {}, {}
    for name in spec.models:
        create, update = generation.storage.inputs[name]
        schemas[f"{name}Create"] = create.model_json_schema()
        schemas[f"{name}Update"] = update.model_json_schema()
    for name, resource in spec.resources.items():
        base = resource.path.rstrip("/") or "/"
        for operation in resource.operations:
            method, path, status = {
                "list": ("get", base, "200"), "get": ("get", base + "/{id}", "200"),
                "create": ("post", base, "201"), "update": ("patch", base + "/{id}", "200"),
                "delete": ("delete", base + "/{id}", "204"),
            }[operation]
            entry = {"operationId": f"{name}_{operation}", "responses": {status: {"description": "Success"}}}
            if spec.auth.mode in ("database", "verified"):
                entry["x-looprd-required-scopes"] = resource.permissions[operation]
                entry["responses"].update({"401": {"description": "Invalid or expired API key"},
                                           "403": {"description": "Insufficient scope"}})
            if "{id}" in path:
                entry["parameters"] = [{"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}]
            if operation == "list":
                entry["parameters"] = [
                    {"name": "limit", "in": "query", "schema": {"type": "integer", "minimum": 1, "maximum": 100}},
                    {"name": "offset", "in": "query", "schema": {"type": "integer", "minimum": 0}},
                ]
            if operation in ("create", "update"):
                suffix = "Create" if operation == "create" else "Update"
                entry["requestBody"] = {"required": True, "content": {"application/json": {
                    "schema": {"$ref": f"#/components/schemas/{resource.model}{suffix}"},
                }}}
            resource_extensions(entry, resource, operation)
            paths.setdefault(path, {})[method] = entry
    for index, handler in enumerate(spec.handlers):
        paths.setdefault(handler.path, {})[handler.method.lower()] = {
            "operationId": f"handler_{index}", "responses": {str(handler.status): {"description": "Success"}},
        }
        if spec.auth.mode in ("database", "verified"):
            paths[handler.path][handler.method.lower()]["x-looprd-required-scopes"] = handler.scopes
        if handler.access == "anonymous":
            entry = paths[handler.path][handler.method.lower()]
            entry.update({"security": [], "x-looprd-access": "anonymous", "x-looprd-max-response-bytes": 262144})
            entry["responses"]["200"]["content"] = {"application/json": {"schema": {}}}
            entry["responses"]["503"] = {"description": "Anonymous read unavailable; no cached result"}
        if handler.contract:
            entry = paths[handler.path][handler.method.lower()]
            entry["x-looprd-service-contract"] = handler.contract.model_dump()
            entry["requestBody"] = {"required": True, "content": {"application/json": {"schema": handler.contract.request}}}
            entry["responses"][str(handler.status)]["content"] = {"application/json": {"schema": handler.contract.response}}
    infrastructure_paths(spec, paths)
    if spec.realtime.delivery == "durable":
        for resource in spec.websocket.resources:
            paths["/_events/" + resource] = {"get": {
                "operationId": "replay_" + resource,
                "x-looprd-required-scopes": spec.websocket.permissions.get(resource, []),
                "parameters": [{"name": "after", "in": "query", "schema": {"type": "string", "maxLength": 256}},
                               {"name": "limit", "in": "query", "schema": {"type": "integer", "minimum": 1, "maximum": 100}}],
                "responses": {"200": {"description": "Bounded replay page with identity-bound cursor"},
                              "400": {"description": "Invalid cursor"}, "403": {"description": "Forbidden subscription"},
                              "410": {"description": "Retention gap: fetch current state and reset"}}}}
    result = {
        "openapi": "3.1.0", "info": {"title": spec.component, "version": spec.release},
        "servers": [{"url": generation.prefix}], "paths": paths,
        "components": {"schemas": schemas},
        "x-looprd-client-requirements": {"bugReports": {"path": "/_bugreports",
            "apiVersion": "looprd-bugreport/v1", "maxBodyBytes": 65536, "userConsentRequired": True}},
    }
    from .bugreport_contract import openapi_contract
    openapi_contract(paths, schemas)
    from .push_openapi import extend
    extend(spec, paths, schemas)
    if spec.realtime.delivery == "durable":
        result["x-looprd-sync"] = {"apiVersion": "looprd-sync/v1", "snapshotPath": "/_sync/{resource}",
            "replayPath": "/_events/{resource}", "snapshotLifetimeSeconds": 300, "resetStatus": 410,
            "retentionSeconds": spec.realtime.retentionSeconds, "idempotencyField": "eventId"}
    if spec.push:
        result["x-looprd-push"] = {"apiVersion": "looprd-push/v1", "transport": "unifiedpush", "registrationPath": "/_push/devices",
            "verificationRequired": True, "hintOnly": True, "deviceLifetimeSeconds": spec.push.deviceLifetimeSeconds}
    if spec.auth.mode in ("bearer", "database", "verified"):
        result["components"]["securitySchemes"] = {"bearer": {"type": "http", "scheme": "bearer"}}
        result["security"] = [{"bearer": []}]
    if spec.auth.mode in ("database", "verified"):
        result["x-looprd-scopes"] = spec.auth.scopes
    if spec.websocket:
        result["x-looprd-websocket"] = spec.websocket.model_dump()
        result["paths"]["/_ws-ticket"] = {"post": {
            "operationId": "websocket_ticket", "responses": {"200": {"description": "One-use ticket, expires in 30 seconds"}},
        }}
    return result


def resource_extensions(entry, resource, operation):
    if resource.policy:
        entry["x-looprd-record-policy"] = resource.policy.model_dump(exclude_none=True)
    if resource.projection:
        entry["x-looprd-projection"] = resource.projection
    if operation == "list" and resource.query:
        entry["x-looprd-query"] = resource.query.model_dump()
        entry["parameters"].extend([
            {"name": "pagination", "in": "query", "schema": {"enum": ["cursor"]}},
            {"name": "cursor", "in": "query", "schema": {"type": "string", "maxLength": 4096}},
            {"name": "sort", "in": "query", "schema": {"type": "string"}},
        ])
        for field, operators in resource.query.filters.items():
            for operator in operators:
                entry["parameters"].append({"name": "filter." + field + "." + operator,
                    "in": "query", "schema": {"type": "string"}})
    if resource.revisionField and operation in ("update", "delete"):
        entry.setdefault("parameters", []).append({"name": "If-Match", "in": "header",
            "required": True, "schema": {"type": "string"}})
        entry["responses"].update({"412": {"description": "Stale revision"}, "428": {"description": "Revision required"}})


def infrastructure_paths(spec, paths):
    for name, definition in spec.jobs.items():
        if definition.expose:
            paths["/_jobs/" + name] = {"post": {"operationId": "enqueue_" + name,
                "x-looprd-required-scopes": definition.scopes,
                "requestBody": {"required": True, "content": {"application/json": {"schema": {
                    "type": "object", "properties": {"payload": {}, "delaySeconds": {"type": "integer", "minimum": 0, "maximum": 604800}},
                    "additionalProperties": False}}}},
                "responses": {"202": {"description": "Job accepted"}}}}
            paths["/_jobs/" + name + "/{id}"] = {"get": {"operationId": "job_status_" + name,
                "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}],
                "responses": {"200": {"description": "Owner-scoped job status"}}}}
    if spec.dependencies:
        paths["/_services"] = {"get": {"operationId": "declared_services", "responses": {"200": {"description": "Declared contract discovery"}}}}

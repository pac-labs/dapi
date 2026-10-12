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
            if spec.auth.mode == "database":
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
            paths.setdefault(path, {})[method] = entry
    for index, handler in enumerate(spec.handlers):
        paths.setdefault(handler.path, {})[handler.method.lower()] = {
            "operationId": f"handler_{index}", "responses": {str(handler.status): {"description": "Success"}},
        }
        if spec.auth.mode == "database":
            paths[handler.path][handler.method.lower()]["x-looprd-required-scopes"] = handler.scopes
    result = {
        "openapi": "3.1.0", "info": {"title": spec.component, "version": spec.release},
        "servers": [{"url": generation.prefix}], "paths": paths,
        "components": {"schemas": schemas},
    }
    if spec.auth.mode in ("bearer", "database"):
        result["components"]["securitySchemes"] = {"bearer": {"type": "http", "scheme": "bearer"}}
        result["security"] = [{"bearer": []}]
    if spec.auth.mode == "database":
        result["x-looprd-scopes"] = spec.auth.scopes
    if spec.websocket:
        result["x-looprd-websocket"] = spec.websocket.model_dump()
        result["paths"]["/_ws-ticket"] = {"post": {
            "operationId": "websocket_ticket", "responses": {"200": {"description": "One-use ticket, expires in 30 seconds"}},
        }}
    return result

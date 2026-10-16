"""Machine-readable background delivery and snapshot discovery."""

from .push_routes import Registration, Confirmation


def extend(spec, paths, schemas):
    if spec.realtime.delivery == "durable":
        for name in spec.websocket.resources:
            resource = spec.resources[name]
            if "list" not in resource.operations:
                continue
            paths["/_sync/" + name] = {"get": {
                "operationId": "snapshot_" + name,
                "x-looprd-required-scopes": sorted(set(resource.permissions.get("list", []) + spec.websocket.permissions.get(name, []))),
                "parameters": [{"name": "cursor", "in": "query", "schema": {"type": "string", "maxLength": 2048}},
                               {"name": "limit", "in": "query", "schema": {"type": "integer", "minimum": 1, "maximum": 100}}],
                "responses": {"200": {"description": "Projected bootstrap page and before-snapshot replay baseline", "content": {"application/json": {"schema": {
                    "type": "object", "required": ["items", "cursor", "next", "complete"], "properties": {
                        "items": {"type": "array", "items": {"type": "object", "required": ["id", "data"], "properties": {"id": {"type": "integer"}, "data": {"type": "object"}}}},
                        "cursor": {"type": "string"}, "next": {"type": ["string", "null"]}, "complete": {"type": "boolean"}}}}}},
                    "400": {"description": "Invalid cursor"}, "403": {"description": "Insufficient list/subscription permission"},
                    "410": {"description": "Snapshot expired: discard staging and restart"}}}}
    if not spec.push:
        return
    schemas["PushRegistration"] = Registration.model_json_schema()
    schemas["PushConfirmation"] = Confirmation.model_json_schema()
    schemas["PushDevice"] = {"type": "object", "required": ["id", "installationId", "status", "expiresAt"], "properties": {
        "id": {"type": "string"}, "installationId": {"type": "string"}, "status": {"enum": ["pending", "active"]},
        "expiresAt": {"type": "number"}, "verificationExpiresAt": {"type": ["number", "null"]}}}
    paths["/_push/devices"] = {
        "get": operation(spec, "push_devices", {"200": {"description": "Caller-owned devices; endpoint capabilities excluded",
            "content": {"application/json": {"schema": {"type": "object", "properties": {"devices": {"type": "array", "items": {"$ref": "#/components/schemas/PushDevice"}}}}}}}}),
        "post": operation(spec, "push_register", {"200": device_response("Existing verified device renewed"),
            "202": device_response("Pending: receive challenge through the push endpoint, then verify"),
            "409": {"description": "Endpoint registered to another installation"}, "429": {"description": "Device quota"},
            "503": {"description": "Host provider not configured"}}, "PushRegistration")}
    parameter = [{"name": "deviceId", "in": "path", "required": True, "schema": {"type": "string"}}]
    paths["/_push/devices/{deviceId}"] = {"delete": {**operation(spec, "push_remove", {"200": {"description": "Removed"}, "404": {"description": "Device not owned or absent"}}), "parameters": parameter}}
    paths["/_push/devices/{deviceId}/verify"] = {"post": {**operation(spec, "push_verify", {"200": device_response("Device activated"),
        "404": {"description": "Device absent or challenge expired"}}, "PushConfirmation"), "parameters": parameter}}


def device_response(description):
    return {"description": description, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/PushDevice"}}}}


def operation(spec, identity, responses, body=None):
    result = {"operationId": identity, "x-looprd-required-scopes": spec.push.scopes,
        "responses": {"401": {"description": "Authentication required"}, "403": {"description": "Insufficient scope or invalid challenge"},
                      "422": {"description": "Invalid request or endpoint"}, **responses}}
    if body:
        result["requestBody"] = {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/" + body}}}}
    return result

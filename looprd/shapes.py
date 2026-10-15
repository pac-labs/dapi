"""Bounded structural JSON contracts, without remote references or executable patterns."""

import math

ALLOWED = {"type", "properties", "required", "additionalProperties", "items", "maxItems", "minItems",
           "maxLength", "minLength", "minimum", "maximum", "enum", "title", "description"}
TYPES = {"object", "array", "string", "integer", "number", "boolean", "null"}


def check_shape(schema, depth=0):
    if (not isinstance(schema, dict) or depth > 8 or set(schema) - ALLOWED
            or schema.get("type") is not None and (not isinstance(schema["type"], str) or schema["type"] not in TYPES)):
        raise ValueError("contract uses unsupported or excessive schema structure")
    if not isinstance(schema.get("properties", {}), dict) or not isinstance(schema.get("required", []), list) or not isinstance(schema.get("enum", []), list):
        raise ValueError("invalid contract field declarations")
    if any(not isinstance(field, str) for field in schema.get("required", [])):
        raise ValueError("required fields must be names")
    for key in ("minimum", "maximum"):
        if key in schema and (type(schema[key]) not in (int, float) or not math.isfinite(schema[key])):
            raise ValueError("numeric schema bounds must be finite")
    if len(schema.get("properties", {})) > 64 or len(schema.get("enum", [])) > 64:
        raise ValueError("contract exceeds schema bounds")
    for child in schema.get("properties", {}).values():
        check_shape(child, depth + 1)
    if "items" in schema:
        check_shape(schema["items"], depth + 1)
    if not set(schema.get("required", [])) <= schema.get("properties", {}).keys():
        raise ValueError("required contract fields must be declared")
    for field, bound in (("maxLength", 65536), ("minLength", 65536), ("maxItems", 1000), ("minItems", 1000)):
        if field in schema and (type(schema[field]) is not int or not 0 <= schema[field] <= bound):
            raise ValueError("invalid contract size bound")
    if "additionalProperties" in schema and type(schema["additionalProperties"]) is not bool:
        raise ValueError("additionalProperties must be boolean")


def validate(value, schema, depth=0):
    if depth > 8:
        raise ValueError("contract value is too deeply nested")
    kind = schema.get("type")
    matches = {"object": isinstance(value, dict), "array": isinstance(value, list), "string": isinstance(value, str),
               "integer": type(value) is int, "number": type(value) in (int, float),
               "boolean": type(value) is bool, "null": value is None, None: True}
    if not matches[kind] or type(value) is float and not math.isfinite(value):
        raise ValueError("contract type mismatch")
    if "enum" in schema and not any(type(value) is type(item) and value == item for item in schema["enum"]):
        raise ValueError("contract enum mismatch")
    if kind == "object":
        properties = schema.get("properties", {})
        if len(value) > 64 or not set(schema.get("required", [])) <= value.keys():
            raise ValueError("contract object fields mismatch")
        if not schema.get("additionalProperties", False) and set(value) - properties.keys():
            raise ValueError("unexpected contract fields")
        for field, child in properties.items():
            if field in value:
                validate(value[field], child, depth + 1)
    if kind == "array":
        if not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", 100):
            raise ValueError("contract array size mismatch")
        for item in value:
            validate(item, schema.get("items", {}), depth + 1)
    if kind == "string" and not schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", 4096):
        raise ValueError("contract string size mismatch")
    if kind in ("integer", "number") and not schema.get("minimum", -math.inf) <= value <= schema.get("maximum", math.inf):
        raise ValueError("contract numeric bound mismatch")

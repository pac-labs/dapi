"""Exact frontend origins; no device identities or wildcard policies."""

from urllib.parse import urlsplit

from pydantic import Field, field_validator

from .contract import Strict


def validate_origin(value):
    try:
        parsed = urlsplit(value)
        parsed.port  # Validate malformed and out-of-range ports.
    except ValueError as error:
        raise ValueError("invalid frontend origin") from error
    if (not parsed.scheme or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.path or parsed.query or parsed.fragment
            or "*" in value or any(c.isspace() or ord(c) < 32 for c in value)
            or any(c in value for c in "\\?#") or value.endswith(":") or not value.isascii()
            or len(value) > 2048):
        raise ValueError("origin must be an exact scheme://host[:port], without a path or wildcard")
    return value


class Cors(Strict):
    allowedOrigins: list[str] = Field(default_factory=list, max_length=128)

    @field_validator("allowedOrigins")
    @classmethod
    def origins(cls, values):
        if len(values) != len(set(values)):
            raise ValueError("duplicate frontend origin")
        return [validate_origin(value) for value in values]

"""Versioned, bounded application diagnostic submissions and sanitization."""

import re
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import ConfigDict, Field, field_validator

from .contract import Strict

MAX_BODY = 65536
SECRET = re.compile(r"authorization|password|passwd|secret|token|api[-_ ]?key|cookie|credential|session", re.I)


def redact(value):
    value = re.sub(r"\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+", r"\1 [REDACTED]", value, flags=re.I)
    value = re.sub(
        r'''["']?\b([\w-]*(?:authorization|password|passwd|secret|token|api[-_]?key|cookie|credential|session)[\w-]*)["']?\s*[:=]\s*(?:"[^"]*"|'[^']*'|[^\s,;]+)''',
        r"\1=[REDACTED]", value, flags=re.I,
    )

    def url(match):
        try:
            parsed = urlsplit(match.group())
            host = parsed.netloc.rsplit("@", 1)[-1]
            return urlunsplit((parsed.scheme, host, parsed.path, "", ""))
        except ValueError:
            return "[REDACTED URL]"

    return re.sub(r'''https?://[^\s<>"']+''', url, value)


def normalize(value):
    value = re.sub(r"\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b", "<uuid>", value, flags=re.I)
    value = re.sub(r"\b0x[0-9a-f]+\b", "<address>", value, flags=re.I)
    value = re.sub(r"\bline \d+\b", "line ?", value)
    value = re.sub(r"(?<=:)\d+(?::\d+)?(?=[)\s]|$)", "?", value)
    return value.replace("\r\n", "\n").strip()


class BugReport(Strict):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    apiVersion: Literal["looprd-bugreport/v1"]
    appVersion: str = Field(min_length=1, max_length=100)
    category: str = Field(default="application", min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9._-]+$")
    errorCode: str = Field(default="", max_length=100)
    message: str = Field(min_length=1, max_length=4000)
    stackTrace: str = Field(default="", max_length=24000)
    platform: str = Field(default="", max_length=200)
    diagnostics: dict[str, str | int | float | bool | None] = Field(default_factory=dict, max_length=32)

    @field_validator("diagnostics")
    @classmethod
    def bounded_diagnostics(cls, values):
        if any(len(key) > 100 or isinstance(value, str) and len(value) > 1000 for key, value in values.items()):
            raise ValueError("diagnostic entries exceed limits")
        return values

    def sanitized(self):
        body = self.model_dump()
        for key in ("appVersion", "errorCode", "message", "stackTrace", "platform"):
            body[key] = redact(body[key])
        body["diagnostics"] = {
            redact(key): "[REDACTED]" if SECRET.search(key) else redact(value) if isinstance(value, str) else value
            for key, value in body["diagnostics"].items()
        }
        return body


def openapi_contract(paths, schemas):
    schemas["LooprdBugReport"] = BugReport.model_json_schema()
    paths["/_bugreports"] = {"post": {
        "operationId": "submit_bug_report", "summary": "Submit application diagnostics; never grants report read access",
        "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/LooprdBugReport"}}}},
        "responses": {"202": {"description": "Issue stored or deduplicated", "content": {"application/json": {"schema": {
            "type": "object", "required": ["id", "deduplicated", "occurrences"], "properties": {
                "id": {"type": "string"}, "deduplicated": {"type": "boolean"}, "occurrences": {"type": "integer"},
            }}}}}, **{str(code): {"description": description} for code, description in [
                (401, "Invalid authentication"), (413, "Maximum 64 KiB"), (415, "JSON required"),
                (422, "Invalid report"), (429, "Rate limited; honor Retry-After"), (503, "Storage unavailable or component blocked"),
            ]}},
    }}

"""Versioned DAPI/Atrium machine intake DTOs; never approval or publication."""

import hashlib
import json
from typing import Literal

from pydantic import Field, model_validator

from .contract import Strict
from .atrium_sidecar import Component, validate
from .deployment_urls import https_url


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class Provenance(Strict):
    kind: Literal["git", "local", "https"]
    identity: str = Field(pattern=r"^[0-9a-f]{64}$")
    specificationDigest: str = Field(pattern=r"^[0-9a-f]{64}$")
    revision: str | None = Field(pattern=r"^[0-9a-f]{40}$")

    @model_validator(mode="after")
    def real_revision(self):
        if (self.kind == "git") != (self.revision is not None):
            raise ValueError("Git alone has a commit revision")
        return self


class RuntimeLocation(Strict):
    apiBaseUrl: str = Field(max_length=2048, pattern=r"^https://")

    @model_validator(mode="after")
    def valid(self):
        https_url(self.apiBaseUrl)
        return self


class Observation(Strict):
    schema_version: Literal["atrium.discovery-observation/v2"] = Field(alias="schema")
    deploymentId: str = Field(pattern=r"^[a-z][a-z0-9-]{2,95}$")
    component: Component
    operationId: str = Field(pattern=r"^[0-9a-f]{32}$")
    sequence: int = Field(ge=1, le=2**63-1, strict=True)
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    operation: Literal["upsert", "withdraw"]
    source: Provenance
    runtime: RuntimeLocation | None
    sidecar: dict | None
    reason: Literal["sidecar_removed", "component_removed", "component_disabled"] | None

    @model_validator(mode="after")
    def consistent(self):
        if self.operation == "upsert":
            if self.runtime is None or self.sidecar is None or self.reason is not None:
                raise ValueError("upsert requires runtime and metadata")
            validate(self.sidecar, self.component)
        elif self.sidecar is not None or self.runtime is not None or self.reason is None:
            raise ValueError("withdraw requires an explicit reason only")
        body = self.model_dump(by_alias=True)
        for key in ("operationId", "sequence", "fingerprint"):
            body.pop(key)
        if digest(body) != self.fingerprint:
            raise ValueError("observation fingerprint mismatch")
        return self


class Receiver(Strict):
    schema_version: Literal["atrium.discovery-capabilities/v2"] = Field(alias="schema")
    deploymentId: str = Field(max_length=96)
    operations: list[Literal["upsert", "withdraw"]] = Field(min_length=2, max_length=2, json_schema_extra={"uniqueItems": True})
    provenanceKinds: list[Literal["git", "local", "https"]] = Field(min_length=1, max_length=3, json_schema_extra={"uniqueItems": True})

    @model_validator(mode="after")
    def complete(self):
        if set(self.operations) != {"upsert", "withdraw"} or len(set(self.provenanceKinds)) != len(self.provenanceKinds):
            raise ValueError("incomplete receiver capabilities")
        return self


class Acknowledgement(Strict):
    schema_version: Literal["atrium.discovery-ack/v2"] = Field(alias="schema")
    deploymentId: str = Field(max_length=96)
    component: Component
    operationId: str = Field(pattern=r"^[0-9a-f]{32}$")
    sequence: int = Field(ge=1, strict=True)
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["pending", "reviewed", "withdrawn"]
    change: Literal["created", "updated", "unchanged", "withdrawn"]

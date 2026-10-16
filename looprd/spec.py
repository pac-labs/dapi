"""Strict, self-contained component and sources-file contracts."""

import re
from typing import Any, Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from .contract import Name, Symbol, PathName, Scope, Strict
from .platform_spec import Reference, Index, Query, ServiceContract, Dependency, Limits, Job
from .cors_spec import Cors
from .administration_spec import Management, check_management


class Verifier(Strict):
    kind: Literal["python"] = "python"
    source: str = Field(min_length=1, max_length=500000)


class Auth(Strict):
    mode: Literal["public", "bearer", "database", "verified"]
    secretRef: str | None = None
    scopes: list[Scope] = Field(default_factory=list)
    verifier: Verifier | None = None
    realm: Name | None = None

    @model_validator(mode="after")
    def bearer_secret(self):
        if (self.mode == "bearer") != bool(self.secretRef):
            raise ValueError("only bearer requires secretRef")
        if len(self.scopes) != len(set(self.scopes)):
            raise ValueError("duplicate auth scopes")
        if (self.mode in ("database", "verified")) != bool(self.scopes):
            raise ValueError("database and verified modes require declared scopes")
        if (self.mode == "verified") != bool(self.verifier):
            raise ValueError("only verified mode requires a verifier")
        if self.realm is not None and self.mode != "verified":
            raise ValueError("realm applies only to verified authentication")
        return self


class API(Strict):
    version: Annotated[str, StringConstraints(pattern=r"^v[1-9][0-9]*$")]


class Column(Strict):
    type: Literal["integer", "string", "boolean", "float", "datetime", "json"]
    required: bool = False
    primaryKey: bool = False
    generated: bool = False
    unique: bool = False
    maxLength: int | None = Field(default=None, gt=0, le=65536)
    references: Reference | None = None

    @model_validator(mode="after")
    def constraints(self):
        if self.type == "string" and self.maxLength is None:
            raise ValueError("string fields require maxLength")
        if self.type != "string" and self.maxLength is not None:
            raise ValueError("maxLength applies only to strings")
        if self.generated and not self.primaryKey:
            raise ValueError("only primary keys can be generated")
        if self.primaryKey and (self.type != "integer" or not self.generated):
            raise ValueError("primary key must be a generated integer")
        return self


class Model(Strict):
    fields: dict[Symbol, Column] = Field(min_length=1)
    indexes: list[Index] = Field(default_factory=list, max_length=32)
    unique: list[list[Symbol]] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def primary_key(self):
        if sum(f.primaryKey for f in self.fields.values()) != 1:
            raise ValueError("exactly one primary key is required")
        return self


class Migration(Strict):
    version: int = Field(ge=2)
    statements: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def additive_only(self):
        for statement in self.statements:
            clean = statement.strip().removesuffix(";")
            if ";" in clean or "--" in clean or "/*" in clean or not re.match(
                r'^(ALTER\s+TABLE\s+(?:[A-Za-z][A-Za-z0-9_]*|"[A-Za-z][A-Za-z0-9_]*")\s+ADD\s+COLUMN\s+|CREATE\s+(?:UNIQUE\s+)?INDEX\s+|CREATE\s+TABLE\s+)',
                clean, flags=re.IGNORECASE,
            ):
                raise ValueError("automatic migrations support additive DDL statements only")
        return self


class Database(Strict):
    backend: Literal["sqlite", "postgresql"] = "sqlite"
    schemaVersion: int = Field(default=1, ge=1)
    connectionSecret: str | None = None
    migrations: list[Migration] = Field(default_factory=list)

    @model_validator(mode="after")
    def connection(self):
        if (self.backend == "postgresql") != bool(self.connectionSecret):
            raise ValueError("postgresql requires connectionSecret; sqlite cannot use one")
        versions = [m.version for m in self.migrations]
        if versions != list(range(2, self.schemaVersion + 1)):
            raise ValueError("include ordered migrations 2 through schemaVersion")
        return self


class Membership(Strict):
    model: Symbol
    subjectField: Symbol
    membershipField: Symbol
    recordField: Symbol


class AccessPolicy(Strict):
    ownerField: Symbol | None = None
    membership: Membership | None = None

    @model_validator(mode="after")
    def nonempty(self):
        if not self.ownerField and not self.membership:
            raise ValueError("access policy must declare ownership or membership")
        return self


class Resource(Strict):
    model: Symbol
    path: PathName
    operations: list[Literal["list", "get", "create", "update", "delete"]] = Field(min_length=1)
    permissions: dict[str, list[Scope]] = Field(default_factory=dict)
    policy: AccessPolicy | None = None
    query: Query | None = None
    projection: list[Symbol] | None = Field(default=None, min_length=1, max_length=64)
    revisionField: Symbol | None = None


class WebSocket(Strict):
    path: PathName = "/events"
    resources: list[Name] = Field(min_length=1)
    permissions: dict[Name, list[Scope]] = Field(default_factory=dict)


class Handler(Strict):
    path: PathName
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "GET"
    kind: Literal["response", "python"] = "response"
    response: Any = None
    status: int = Field(default=200, ge=200, le=599)
    source: str | None = None
    scopes: list[Scope] = Field(default_factory=list)
    contract: ServiceContract | None = None
    execution: Literal["inline", "process"] = "inline"
    timeoutSeconds: int | None = Field(default=None, ge=1, le=60)
    secrets: list[str] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def code(self):
        if (self.kind == "python") != bool(self.source):
            raise ValueError("python handlers require inline source; response handlers cannot use source")
        if self.kind != "python" and (self.execution != "inline" or self.timeoutSeconds or self.secrets):
            raise ValueError("execution options apply only to Python handlers")
        return self


class Realtime(Strict):
    backend: Literal["memory", "redis"] = "memory"
    delivery: Literal["best-effort", "durable"] = "best-effort"
    connectionSecret: str | None = None
    retentionSeconds: int = Field(default=86400, ge=60, le=604800)
    maxEvents: int = Field(default=100000, ge=100, le=1000000)
    maxEventBytes: int = Field(default=65536, ge=1024, le=1047000)
    maxReplayBytes: int = Field(default=1048576, ge=1048576, le=8388608)

    @model_validator(mode="after")
    def connection(self):
        if (self.backend == "redis") != bool(self.connectionSecret):
            raise ValueError("only Redis realtime requires connectionSecret")
        return self


class Specification(Strict):
    apiVersion: Literal["looprd/v1"]
    component: Name
    release: str = Field(min_length=1, max_length=100)
    api: API
    auth: Auth
    cors: Cors | None = None
    management: Management | None = None
    database: Database | None = None
    models: dict[Symbol, Model] = Field(default_factory=dict)
    resources: dict[Name, Resource] = Field(default_factory=dict)
    websocket: WebSocket | None = None
    handlers: list[Handler] = Field(default_factory=list)
    realtime: Realtime = Field(default_factory=Realtime)
    requires: list[Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9.:-]{0,95}$")]] = Field(default_factory=list, max_length=64)
    dependencies: list[Dependency] = Field(default_factory=list, max_length=64)
    limits: Limits = Field(default_factory=Limits)
    jobs: dict[Name, Job] = Field(default_factory=dict, max_length=64)

    @model_validator(mode="after")
    def references(self):
        from .spec_data_checks import data_contracts
        data_contracts(self)
        from .capabilities import require_capabilities
        require_capabilities(self.requires)
        if self.jobs and not self.database:
            raise ValueError("jobs require a component database")
        for job in self.jobs.values():
            if job.expose and self.auth.mode in ("database", "verified"):
                if not job.scopes or not set(job.scopes) <= set(self.auth.scopes):
                    raise ValueError("exposed jobs require declared scopes in scoped authentication modes")
            elif job.scopes:
                raise ValueError("job scopes apply only to exposed jobs with scoped authentication")
        if len(self.requires) != len(set(self.requires)):
            raise ValueError("duplicate required capabilities")
        if self.realtime.delivery == "durable" and (not self.database or not self.websocket):
            raise ValueError("durable events require database and WebSocket resources")
        self.check_permissions()
        check_management(self)
        if (self.models or self.resources) and not self.database:
            raise ValueError("models/resources require database")
        occupied = {"/_ws-ticket", "/openapi.json", "/_events", "/_services", "/_jobs", "/_bugreports"}
        for resource in self.resources.values():
            if resource.model not in self.models:
                raise ValueError("unknown resource model")
            if resource.policy:
                if self.auth.mode not in ("database", "verified"):
                    raise ValueError("private policies require individual verified or database principals")
                fields = self.models[resource.model].fields
                owner = resource.policy.ownerField
                if owner and (owner not in fields or fields[owner].type != "string" or fields[owner].maxLength < 64):
                    raise ValueError("owner field must be a string accommodating the stable principal ID")
                membership = resource.policy.membership
                if membership:
                    related = self.models.get(membership.model)
                    if (not related or membership.subjectField not in related.fields
                            or membership.membershipField not in related.fields or membership.recordField not in fields):
                        raise ValueError("membership policy references unknown fields")
                    subject = related.fields[membership.subjectField]
                    if subject.type != "string" or subject.maxLength < 64:
                        raise ValueError("membership subject field must accommodate the stable principal ID")
                    if related.fields[membership.membershipField].type != fields[membership.recordField].type:
                        raise ValueError("membership key types must match")
            if resource.path == "/":
                raise ValueError("resource collections require a named path")
            if len(resource.operations) != len(set(resource.operations)):
                raise ValueError("duplicate resource operations")
            path = resource.path.rstrip("/") or "/"
            if path in occupied or any(path.startswith(p + "/") or p.startswith(path + "/") for p in occupied):
                raise ValueError("overlapping/reserved resource path")
            occupied.add(path)
        custom = set()
        for handler in self.handlers:
            path = handler.path.rstrip("/") or "/"
            if path in occupied or any(path.startswith(p + "/") or p.startswith(path + "/") for p in occupied):
                raise ValueError("handler overlaps resource/reserved path")
            if (path, handler.method) in custom:
                raise ValueError("duplicate handler route")
            custom.add((path, handler.method))
        if self.websocket:
            if not set(self.websocket.resources) <= self.resources.keys():
                raise ValueError("unknown WebSocket resource")
            if self.websocket.path.rstrip("/") in occupied | {p for p, _ in custom}:
                raise ValueError("WebSocket route conflicts")
        return self

    def check_permissions(self):
        database_auth = self.auth.mode in ("database", "verified")
        if self.auth.mode == "database" and not self.database:
            raise ValueError("database authentication requires database")
        requirements = []
        for resource in self.resources.values():
            expected = set(resource.operations) if database_auth else set()
            if set(resource.permissions) != expected:
                raise ValueError("permissions must cover exactly the exposed operations in scoped authentication modes")
            requirements.extend(resource.permissions.values())
        if self.websocket:
            expected = set(self.websocket.resources) if database_auth else set()
            if set(self.websocket.permissions) != expected:
                raise ValueError("WebSocket permissions must cover exactly its resources in scoped authentication modes")
            requirements.extend(self.websocket.permissions.values())
        for handler in self.handlers:
            if bool(handler.scopes) != database_auth:
                raise ValueError("database and verified handlers require scopes")
            if handler.scopes:
                requirements.append(handler.scopes)
        for scopes in requirements:
            if not scopes or len(scopes) != len(set(scopes)) or not set(scopes) <= set(self.auth.scopes):
                raise ValueError("permissions require nonempty, unique, declared scopes")


class Source(Strict):
    id: Name
    specification: str = "looprd.yaml"
    repository: str | None = None
    ref: str = "main"
    enabled: bool = True

    @model_validator(mode="after")
    def location(self):
        if self.repository:
            if not re.fullmatch(r"https://github\.com/[\w.-]+/[\w.-]+(?:\.git)?", self.repository):
                raise ValueError("repository must be a GitHub HTTPS repository URL")
            parts = self.specification.replace("\\", "/").split("/")
            if any(p in ("", ".", "..") for p in parts) or ":" in self.specification:
                raise ValueError("repository specification must be a relative file path")
        return self


class Sources(Strict):
    apiVersion: Literal["looprd/v1"]
    components: list[Source]

    @model_validator(mode="after")
    def unique(self):
        if len({s.id for s in self.components}) != len(self.components):
            raise ValueError("duplicate source IDs")
        return self


class UniqueLoader(yaml.SafeLoader):
    pass


def unique_mapping(loader, node):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if key in result:
            raise ValueError("duplicate YAML key")
        result[key] = loader.construct_object(value_node)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def parse_document(content: str):
    if len(content.encode()) > 1_048_576:
        raise ValueError("document exceeds 1 MiB")
    return yaml.load(content, Loader=UniqueLoader)

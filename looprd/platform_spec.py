"""Opt-in, provider-independent data and service contracts."""

from typing import Literal

from pydantic import Field, model_validator

from .contract import Strict, Symbol, Name, Scope


class Reference(Strict):
    model: Symbol
    field: Symbol


class Index(Strict):
    name: Symbol = Field(max_length=63)
    fields: list[Symbol] = Field(min_length=1, max_length=8)
    unique: bool = False


class Query(Strict):
    filters: dict[Symbol, list[Literal["eq", "ne", "lt", "le", "gt", "ge", "in", "prefix"]]] = Field(default_factory=dict)
    sort: list[Symbol] = Field(default_factory=list, max_length=16)


class ServiceContract(Strict):
    name: Name
    version: int = Field(ge=1, le=100000)
    request: dict = Field(default_factory=dict)
    response: dict = Field(default_factory=dict)
    serviceOnly: bool = True
    allowedCallers: list[Name] = Field(default_factory=list, max_length=64)


class Dependency(Strict):
    component: Name
    contract: Name
    version: int = Field(ge=1, le=100000)
    credentialSecret: str = Field(min_length=1, max_length=256)


class Limits(Strict):
    backend: Literal["memory", "redis"] = "memory"
    connectionSecret: str | None = None
    requestsPerMinute: int | None = Field(default=None, ge=1, le=1000000)
    authAttemptsPerMinute: int | None = Field(default=None, ge=1, le=1000000)
    burstPerSecond: int | None = Field(default=None, ge=1, le=100000)
    maxInFlight: int | None = Field(default=None, ge=1, le=1000)
    maxRows: int | None = Field(default=None, ge=1, le=10000000)
    maxWebSockets: int | None = Field(default=None, ge=1, le=10000)

    @model_validator(mode="after")
    def connection(self):
        if (self.backend == "redis") != bool(self.connectionSecret):
            raise ValueError("only Redis limits require a connectionSecret")
        return self


class Job(Strict):
    source: str = Field(min_length=1, max_length=500000)
    execution: Literal["inline", "process"] = "process"
    intervalSeconds: int | None = Field(default=None, ge=60, le=31536000)
    timeoutSeconds: int = Field(default=30, ge=1, le=60)
    maxAttempts: int = Field(default=3, ge=1, le=10)
    retrySeconds: int = Field(default=30, ge=1, le=3600)
    expose: bool = False
    scopes: list[Scope] = Field(default_factory=list)
    secrets: list[str] = Field(default_factory=list, max_length=16)

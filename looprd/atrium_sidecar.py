"""Metadata-only compatibility model for Atrium atriumd/v1 (2026-10-18).

Contract owner: TheBigTreeInc/atrium, revision
286db965a9428422c366b0b3b1138190b2b649e0, src/atriumd_contract.py.
No code from the component repository is imported or executed during discovery.
"""

import re
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, StringConstraints, field_validator, model_validator

from .contract import Strict

Component = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]{0,47}$")]
Identifier = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]{0,63}$")]


class Metadata(Strict):
    model_config = ConfigDict(extra="forbid", strict=True)


class Platform(Metadata):
    runtime: Literal["web", "windows", "linux", "macos", "android", "ios"]
    formFactors: list[Literal["desktop", "tablet", "phone"]] = Field(min_length=1, max_length=3)
    installMode: Literal["none", "pwa", "offline-web", "native"]

    @model_validator(mode="after")
    def compatible(self):
        if (len(set(self.formFactors)) != len(self.formFactors)
                or self.runtime != "web" and self.installMode in ("pwa", "offline-web")
                or self.runtime == "web" and self.installMode == "native"):
            raise ValueError("incompatible sidecar platform")
        return self


class Launch(Metadata):
    mode: Literal["hosted-web"]
    originRef: Identifier
    entrypoint: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,200}$")]

    @field_validator("entrypoint")
    @classmethod
    def descendant(cls, value):
        if any(part in ("", ".", "..") for part in value.split("/")):
            raise ValueError("unsafe sidecar entrypoint")
        return value


class Service(Metadata):
    component: Component
    role: Literal["multiplayer", "activity", "achievements", "content"]


class Integration(Metadata):
    atriumOptional: Literal[True]
    sessions: Literal["game-owned"]
    state: Literal["game-owned"]
    identity: Literal["game-owned"]

    @field_validator("atriumOptional", mode="before")
    @classmethod
    def explicit_true(cls, value):
        if type(value) is not bool or value is not True:
            raise ValueError("Atrium integration must be optional")
        return value


class Game(Metadata):
    id: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9.-]{2,119}$")]
    publisher: Identifier
    title: str
    summary: str | None = None
    kind: Literal["game"]
    platforms: list[Platform] = Field(min_length=1, max_length=12)
    launch: Launch
    services: list[Service] = Field(default_factory=list, max_length=12)
    integration: Integration | None = None

    @field_validator("title", "summary")
    @classmethod
    def bounded_text(cls, value, info):
        if value is None or not 1 <= len(value.strip()) <= (160 if info.field_name == "title" else 300):
            raise ValueError("invalid sidecar text")
        return value

    @field_validator("integration", mode="before")
    @classmethod
    def non_null(cls, value):
        if value is None:
            raise ValueError("invalid sidecar integration")
        return value

    @model_validator(mode="after")
    def unique(self):
        runtimes = [platform.runtime for platform in self.platforms]
        services = [(service.component, service.role) for service in self.services]
        if "web" not in runtimes or len(set(runtimes)) != len(runtimes) or len(set(services)) != len(services):
            raise ValueError("duplicate or missing sidecar platform/service")
        return self


class Sidecar(Metadata):
    schema_version: Literal["atriumd/v1"] = Field(alias="schema")
    component: Component
    game: Game


def validate(document, component):
    value = Sidecar.model_validate(document)
    if value.component != component:
        raise ValueError("sidecar component mismatch")
    def check(item):
        if isinstance(item, str) and (re.search(r"(?i)\b(?:https?|wss?|file)://", item) or item.startswith(("/", "\\"))):
            raise ValueError("sidecar contains a deployment address")
        if isinstance(item, dict):
            for child in item.values():
                check(child)
        if isinstance(item, list):
            for child in item:
                check(child)
    check(value.model_dump(by_alias=True, exclude_defaults=True))
    return document

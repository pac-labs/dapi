"""Opt-in background wake hints; applications retain recipient policy."""

from typing import Literal

from pydantic import Field

from .contract import Scope, Strict


class Push(Strict):
    transport: Literal["unifiedpush"] = "unifiedpush"
    scopes: list[Scope] = Field(min_length=1, max_length=32)
    deviceLifetimeSeconds: int = Field(default=86400, ge=600, le=604800)
    maxDevicesPerPrincipal: int = Field(default=8, ge=1, le=32)
    maxDevices: int = Field(default=5000, ge=1, le=50000)
    retrySeconds: int = Field(default=3600, ge=60, le=86400)


def check_push(spec):
    if spec.push is None:
        return
    if spec.auth.mode not in ("database", "verified") or spec.realtime.delivery != "durable":
        raise ValueError("push requires individual database/verified authentication and durable events")
    if len(spec.push.scopes) != len(set(spec.push.scopes)) or not set(spec.push.scopes) <= set(spec.auth.scopes):
        raise ValueError("push registration requires unique declared scopes")

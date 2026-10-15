"""Optional trusted server-side verification; no account or provider infrastructure."""

import hashlib
import inspect
import time
from types import SimpleNamespace
from typing import Literal

from pydantic import Field
from starlette.concurrency import run_in_threadpool

from .auth import Principal
from .spec import Scope, Strict


class VerifiedIdentity(Strict):
    subject: str = Field(min_length=1, max_length=256)
    label: str = Field(default="verified identity", max_length=100)
    scopes: list[Scope] = Field(max_length=128)
    sessionId: str | None = Field(default=None, max_length=256)
    expiresAt: float | None = Field(default=None, allow_inf_nan=False)
    kind: Literal["user", "service"] = "user"


class Verification:
    def __init__(self, generation):
        self.generation = generation
        self.error = None
        if not generation.settings.allow_python:
            raise ValueError("trusted Python verification requires LOOPRD_ALLOW_PYTHON=true")
        namespace = {"__name__": "looprd_verification_" + generation.spec.component}
        exec(compile(generation.spec.auth.verifier.source, "<looprd-verifier>", "exec"), namespace)
        self.verify = namespace.get("verify")
        if not inspect.iscoroutinefunction(self.verify):
            raise ValueError("verifier must define async def verify(ctx)")

    async def authenticate(self, authorization):
        if not authorization or not authorization.startswith("Bearer ") or not 8 <= len(authorization) <= 8192:
            return None
        credential = authorization[7:]
        if not credential.strip():
            return None
        generation = self.generation
        context = SimpleNamespace(credential=credential,
            session_factory=generation.storage.sessions if generation.storage else None,
            secret=generation.settings.secret, run=run_in_threadpool)
        try:
            result = await self.verify(context)
            if result is None:
                self.error = None
                return None
            identity = VerifiedIdentity.model_validate(result)
            if (len(identity.scopes) != len(set(identity.scopes))
                    or not set(identity.scopes) <= set(generation.spec.auth.scopes)
                    or identity.expiresAt is not None and identity.expiresAt <= time.time()):
                raise ValueError("invalid verified permissions or expiry")
            realm = generation.spec.auth.realm or generation.spec.component
            principal_id = hashlib.sha256((realm + "\0" + identity.subject).encode()).hexdigest()
            self.error = None
            return Principal(principal_id, identity.label, frozenset(identity.scopes), identity.kind, realm,
                             identity.subject, identity.sessionId, identity.expiresAt, authorization)
        except Exception as error:
            # Trusted plugin failures never promote request parameters to an identity.
            self.error = type(error).__name__
            return None

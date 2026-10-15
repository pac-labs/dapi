"""Typed, declared local component calls using verified machine credentials."""

from contextvars import ContextVar

import httpx

from .outbox import EventError

DEPTH = ContextVar("looprd_service_depth", default=0)


def contract(generation, name, version):
    return next((handler for handler in generation.spec.handlers
                 if handler.contract and handler.contract.name == name and handler.contract.version == version), None)


def discover(runtime, generation):
    result = []
    for dependency in generation.spec.dependencies:
        target = runtime.active.get(dependency.component)
        handler = contract(target, dependency.contract, dependency.version) if target else None
        result.append({"component": dependency.component, "contract": dependency.contract,
            "version": dependency.version, "available": bool(handler and dependency.component not in runtime.availability.blocked),
            "endpoint": target.prefix + handler.path if handler else None,
            "request": handler.contract.request if handler else None, "response": handler.contract.response if handler else None})
    return result


async def call(runtime, generation, component, name, payload=None):
    dependency = next((item for item in generation.spec.dependencies if item.component == component and item.contract == name), None)
    if not dependency:
        raise EventError(403, "undeclared service dependency")
    if DEPTH.get() >= 8:
        raise EventError(508, "service call nesting limit reached")
    target = runtime.active.get(component)
    handler = contract(target, name, dependency.version) if target else None
    if not handler or component in runtime.availability.blocked:
        raise EventError(503, "required service is unavailable")
    credential = generation.settings.secret(dependency.credentialSecret)
    principal = await target.authenticate("Bearer " + credential)
    if not principal or principal.kind != "service" or principal.subject != generation.spec.component:
        raise EventError(403, "service credential is not bound to the calling component")
    token = DEPTH.set(DEPTH.get() + 1)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=runtime.app), base_url="http://looprd.internal") as client:
            response = await client.request(handler.method, target.prefix + handler.path, json=payload,
                headers={"Authorization": "Bearer " + credential}, timeout=generation.settings.request_seconds)
        if not response.is_success:
            raise EventError(response.status_code, "service call was rejected")
        return response.json()
    finally:
        DEPTH.reset(token)


def dependency_errors(generations):
    errors = {}
    for name, generation in generations.items():
        for dependency in generation.spec.dependencies:
            target = generations.get(dependency.component)
            if not target or not contract(target, dependency.contract, dependency.version):
                errors[name] = "required service contract unavailable"
    return errors

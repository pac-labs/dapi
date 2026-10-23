"""Generic scoped pull inventory and source-bound opaque documents."""

import hashlib
import hmac
import json
import re

import httpx
from starlette.responses import JSONResponse

from .deployment_urls import https_url
from .spec_imports import child_location

HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
NAME = re.compile(r"^[a-z][a-z0-9-]{0,47}$")


def reply(body, status=200):
    return JSONResponse(body, status_code=status, headers=HEADERS)


def reader_service(settings, authorization):
    """Only designated reader secrets; management/user credentials confer no grant."""
    try:
        readers = json.loads(settings.registration_readers)
        if not isinstance(readers, dict) or len(readers) > 32:
            raise ValueError()
        if any(not NAME.fullmatch(name) or not isinstance(ref, str) or not 1 <= len(ref) <= 256
               for name, ref in readers.items()):
            raise ValueError()
    except (ValueError, TypeError):
        return None, 503
    if not authorization.startswith("Bearer ") or not 32 <= len(authorization[7:]) <= 512:
        return None, 401
    matched = []
    for service, reference in readers.items():
        try:
            secret = settings.secret(reference)
            if not 32 <= len(secret) <= 512:
                continue
            if hmac.compare_digest(authorization[7:].encode(), secret.encode()):
                matched.append(service)
        except (OSError, ValueError):
            continue
    # Reusing the same secret across services must not make authorization ambiguous.
    return (matched[0], None) if len(matched) == 1 else (None, 401)


def inventory(runtime, service):
    items = []
    origin = None
    try:
        if runtime.settings.public_url:
            origin = https_url(runtime.settings.public_url, origin_only=True)
    except ValueError:
        pass  # Invalid optional registration configuration cannot unload APIs.
    for component, generation in sorted(runtime.active.items()):
        for registration in generation.spec.registrations:
            if registration.service != service:
                continue
            if len(items) >= 1000:
                return reply({"error": "registration_inventory_limit"}, 503)
            path = "/_looprd/registrations/" + component + "/" + service + "/document"
            snapshot = runtime.reader.snapshots.get(component)
            available = bool(snapshot and snapshot.digest == generation.digest
                             and snapshot.revision == runtime.revisions.get(component))
            items.append({"component": component, "service": service,
                "release": generation.spec.release, "generation": generation.digest,
                "apiPath": generation.prefix, "apiBaseUrl": origin + generation.prefix if origin else None,
                "blocked": component in runtime.availability.blocked, "document": registration.document,
                "source": snapshot.provenance() if available else None, "documentPath": path})
    return reply({"schema": "looprd.registrations/v1", "service": service,
        "deploymentId": runtime.settings.deployment_id,
        "complete": not runtime.sources_error and not runtime.errors and not runtime.monitor.checking,
        "registrations": items})


async def document(runtime, component, service, request):
    generation = runtime.active.get(component)
    registration = next((r for r in generation.spec.registrations if r.service == service), None) if generation else None
    if not registration:
        return reply({"error": "registration_not_found"}, 404)
    if request.query_params.get("generation") != generation.digest:
        return reply({"error": "registration_generation_required_or_changed"}, 409)
    snapshot = runtime.reader.snapshots.get(component)
    if (not snapshot or snapshot.digest != generation.digest
            or snapshot.revision != runtime.revisions.get(component)):
        return reply({"error": "registration_source_unavailable"}, 503)
    if getattr(runtime, "registration_reads", 0) >= 4:
        return reply({"error": "registration_reads_busy"}, 429)
    runtime.registration_reads = getattr(runtime, "registration_reads", 0) + 1
    try:
        location = child_location(snapshot.location, registration.document)
        content = await runtime.reader.read(location, max_bytes=65536)
        if len(content.encode("utf-8")) > 65536:
            return reply({"error": "registration_document_too_large"}, 503)
        if (runtime.active.get(component) is not generation
                or runtime.revisions.get(component) != snapshot.revision
                or runtime.reader.snapshots.get(component) != snapshot):
            return reply({"error": "registration_generation_changed"}, 409)
        return reply({"schema": "looprd.registration-document/v1", "component": component,
            "service": service, "generation": generation.digest, "source": snapshot.provenance(),
            "consistency": "git-pinned" if snapshot.provenance()["kind"] == "git" else "best-effort",
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(), "content": content})
    except (OSError, ValueError, httpx.HTTPError):
        return reply({"error": "registration_source_unavailable"}, 503)
    finally:
        runtime.registration_reads -= 1


async def endpoint(request):
    runtime = request.app.state.runtime
    service, error = reader_service(runtime.settings, request.headers.get("authorization", ""))
    if error:
        return reply({"error": "registration_readers_unavailable" if error == 503 else "unauthorized"}, error)
    if not request.path_params:
        return inventory(runtime, service)
    if request.path_params["service"] != service:
        return reply({"error": "registration_service_forbidden"}, 403)
    return await document(runtime, request.path_params["component"], service, request)

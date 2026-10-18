"""Credential-free operational snapshots for the authenticated dashboard."""

import time
from datetime import datetime, timezone
from .cors import allowed_origins


def timestamp():
    return datetime.now(timezone.utc).isoformat()


class Monitor:
    def __init__(self):
        self.started_at = timestamp()
        self.started = time.monotonic()
        self.checking = False
        self.last_attempt = None
        self.last_finished = None
        self.last_success = None
        self.next_poll_at = None

    def begin(self):
        self.checking = True
        self.last_attempt = timestamp()

    def finish(self, success):
        self.checking = False
        self.last_finished = timestamp()
        if success:
            self.last_success = self.last_finished


def routes(spec):
    result = []
    for name, resource in spec.resources.items():
        for operation in resource.operations:
            method, item = {
                "list": ("GET", False), "get": ("GET", True),
                "create": ("POST", False), "update": ("PATCH", True),
                "delete": ("DELETE", True),
            }[operation]
            result.append({"method": method, "path": resource.path.rstrip("/") + ("/{id}" if item else ""),
                           "kind": "resource", "name": name,
                           "scopes": resource.permissions.get(operation, [])})
    for handler in spec.handlers:
        result.append({"method": handler.method, "path": handler.path,
                       "kind": handler.kind, "scopes": handler.scopes})
    result.append({"method": "GET", "path": "/openapi.json", "kind": "documentation", "scopes": []})
    if spec.websocket:
        result.append({"method": "WS", "path": spec.websocket.path,
                       "kind": "subscription", "scopes": []})
        result.append({"method": "POST", "path": "/_ws-ticket", "kind": "ticket", "scopes": []})
    return result


def snapshot(runtime):
    monitor, settings = runtime.monitor, runtime.settings
    components = {}
    for key, value in runtime.active.items():
        spec = value.spec
        components[key] = {
            "release": spec.release, "revision": runtime.revisions[key],
            "digest": value.digest, "prefix": value.prefix,
            "schemaVersion": spec.database.schemaVersion if spec.database else None,
            "databaseBackend": spec.database.backend if spec.database else None,
            "authMode": spec.auth.mode, "scopes": spec.auth.scopes,
            "allowedOrigins": allowed_origins(settings, spec),
            "dataAdministrationEnabled": value.administration is not None,
            "blocked": key in runtime.availability.blocked,
            "error": runtime.errors.get(key), "loadedAt": value.loaded_at,
            "activeRequests": value.active_requests, "websocketConnections": len(value.sockets),
            "routes": routes(spec),
            "models": {name: {field: column.model_dump(exclude_none=True)
                              for field, column in model.fields.items()}
                       for name, model in spec.models.items()},
            "websocket": spec.websocket.model_dump() if spec.websocket else None,
            "realtime": {**value.bus.status(), "delivery": spec.realtime.delivery, "relayError": value.relay_error},
            "verificationError": value.verifier.error if value.verifier else None,
            "push": {"enabled": spec.push is not None, "configured": bool(settings.push_public_url), "error": value.push_error},
        }
    return {
        "sourcesError": runtime.sources_error, "components": components,
        "errors": dict(runtime.errors), "sources": dict(runtime.sources),
        "blocked": sorted(runtime.availability.blocked),
        "runtime": {
            "startedAt": monitor.started_at, "uptimeSeconds": int(time.monotonic() - monitor.started),
            "pollSeconds": settings.poll_seconds, "drainSeconds": settings.drain_seconds,
            "checking": monitor.checking, "lastAttemptAt": monitor.last_attempt,
            "lastFinishedAt": monitor.last_finished, "lastSuccessAt": monitor.last_success,
            "retiringGenerations": len(runtime.retiring), "pythonEnabled": settings.allow_python,
            "allowedOrigins": list(settings.allowed_origins),
            "discoveryMode": "observe-only" if runtime.discovery else "unavailable" if settings.discovery_enabled else "disabled",
        },
    }

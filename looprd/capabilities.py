"""Explicit runtime contracts; never advertise planned or partially implemented features."""

CAPABILITIES = frozenset({
    "auth.public", "auth.bearer", "auth.database", "database.sqlite", "database.postgresql",
    "database.migrations.additive", "events.memory", "events.redis", "events.replay",
    "events.transactional", "http.crud", "websocket.resources",
    "auth.verified",
    "authorization.owner", "authorization.membership",
    "database.constraints", "query.filters", "query.keyset", "query.projections", "http.revisions",
    "service.contracts", "service.calls",
    "limits.memory", "limits.redis", "limits.rows", "jobs.leases", "jobs.schedules",
    "execution.process", "operations.local",
    "cors.component",
})


def require_capabilities(required):
    missing = set(required) - CAPABILITIES
    if missing:
        raise ValueError("unsupported required platform capabilities: " + ", ".join(sorted(missing)))

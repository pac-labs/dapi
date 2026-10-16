"""Signed, bounded bootstrap pages plus a before-snapshot durable replay cursor."""

import base64
import hashlib
import hmac
import json
import time

import sqlalchemy as sa
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from .crud import json_safe
from .outbox import EventError
from .replay import allowed


def signature(payload, principal, resource, secret):
    return hmac.new(secret.encode(), (principal.id + "\0" + resource + "\0" + payload).encode(), hashlib.sha256).hexdigest()


def encode(state, principal, resource, secret):
    payload = base64.urlsafe_b64encode(json.dumps(state, separators=(",", ":")).encode()).decode().rstrip("=")
    return payload + "." + signature(payload, principal, resource, secret)


def decode(cursor, principal, resource, secret, digest, floor):
    try:
        if len(cursor) > 2048:
            raise ValueError()
        payload, mac = cursor.split(".")
        if not hmac.compare_digest(mac, signature(payload, principal, resource, secret)):
            raise ValueError()
        state = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        if state["expires"] <= time.time() or state["generation"] != digest or state["sequence"] < floor:
            raise EventError(410, "snapshot expired; restart synchronization")
        return state
    except (ValueError, KeyError, TypeError, UnicodeError):
        raise EventError(400, "invalid snapshot cursor") from None


def page(generation, resource, principal, cursor, limit):
    store, outbox = generation.storage, generation.storage.events
    definition = generation.spec.resources[resource]
    table = store.tables[definition.model]
    pk = next(iter(table.primary_key.columns))
    with store.sessions.begin() as session:
        clock = outbox.locked(session)
        floor, _ = outbox.prune(session, clock)
        policy = store.policies.predicate(resource, principal)
        if cursor:
            state = decode(cursor, principal, resource, clock["secret"], generation.digest, floor)
        else:
            maximum = session.execute(sa.select(sa.func.max(pk)).where(policy)).scalar()
            state = {"sequence": clock["sequence"], "after": None, "max": maximum,
                     "generation": generation.digest, "expires": time.time() + 300}
        baseline = outbox.cursor(resource, principal, state["sequence"], clock["secret"])
        items, size, more = [], 0, False
        if state["max"] is not None:
            condition = sa.and_(policy, pk <= state["max"])
            if state["after"] is not None:
                condition = sa.and_(condition, pk > state["after"])
            rows = session.execute(sa.select(table).where(condition).order_by(pk).limit(limit + 1)).mappings()
            for row in rows:
                item = json_safe({field: row[field] for field in definition.projection} if definition.projection else dict(row))
                cost = len(json.dumps(item).encode())
                if cost > generation.spec.realtime.maxReplayBytes:
                    raise EventError(413, "snapshot record exceeds byte budget")
                if len(items) == limit or items and size + cost > generation.spec.realtime.maxReplayBytes:
                    more = True
                    break
                items.append({"id": row[pk.name], "data": item})
                size += cost
                state["after"] = row[pk.name]
        return {"items": items, "cursor": baseline, "complete": not more,
                "next": encode(state, principal, resource, clock["secret"]) if more else None}


async def snapshot(request, generation, resource):
    if not generation.storage or not generation.storage.events:
        return JSONResponse({"error": "durable events not enabled"}, status_code=404)
    definition = generation.spec.resources.get(resource)
    if (not definition or "list" not in definition.operations or not allowed(generation, resource, request.state.principal)
            or not generation.permits(request.state.principal, definition.permissions.get("list", []))):
        return JSONResponse({"error": "insufficient snapshot permission"}, status_code=403)
    try:
        limit = int(request.query_params.get("limit", "100"))
        if not 1 <= limit <= 100:
            raise ValueError()
    except ValueError:
        return JSONResponse({"error": "limit must be between 1 and 100"}, status_code=422)
    try:
        result = await run_in_threadpool(page, generation, resource, request.state.principal,
                                        request.query_params.get("cursor"), limit)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})
    except EventError as error:
        return JSONResponse({"error": error.message, "resetRequired": error.status == 410},
                            status_code=error.status, headers={"Cache-Control": "no-store"})

# Infrastructure proposal review

Reviewed against GitHub main `e4d4b1b` (2026-10-15). The attachment is a direction
proposal, not an executable descriptor. Its future YAML must not be copied into a
component before the corresponding capabilities are implemented.

| Finding | Judgment and required change |
| --- | --- |
| Redis cannot provide durable chat history through Pub/Sub | Correct. Keep SQL authoritative; implement fanout separately from transactional outbox/replay. |
| PostgreSQL and migrations need adding | Already present. Preserve database identity, additive migration history, and rollback restrictions. |
| Existing keys are user identities | Incorrect if inferred. Component-scoped keys are not user accounts, OIDC audiences, membership checks, or row permissions. |
| Resource subscriptions can become private conversation topics | Requires a separate authorization/wire-contract design. Existing event scopes expose complete resource records; Redis must not widen that access. |
| Redis permits multiple workers | Insufficient. Tickets, reconcile locks/checkpoints, blocks, file invitations, migrations, and reloads remain process/node-local. One ASGI worker remains required. |
| Change apiVersion to looprd/v2 for Redis | Unnecessary for an optional transport with unchanged client messages. Add an optional looprd/v1 field and retain the memory default. |
| Preserve inline handlers | Required. ctx.publish(event) stays synchronous; neither Redis latency nor outages may turn an already committed CRUD operation into a failed HTTP response. |
| Reload shares a single bus | Preserve the local bus across generations, but reference-count Redis connections so failed candidates, removal, and draining cannot leak listeners or disrupt active generations. |
| Isolate environments with Redis database numbers | Unsafe: Pub/Sub ignores database selection. Use a host namespace plus component and API version in exact channel names. |

## Accepted first implementation

Optional `realtime.backend: redis`, with `connectionSecret` referencing a local
Redis URL file and `delivery: best-effort`. Omission or explicit memory preserves
existing behavior. Redis adds cross-runtime notification fanout, bounded sending,
origin suppression, duplicate suppression during overlapping reload connections,
reconnect/backoff, and sanitized status. Missing/invalid connection configuration
rejects a candidate. An unavailable Redis server degrades remote notifications;
local events and component HTTP/database operations continue.

The companion uses a dedicated private Podman network, no published Redis port,
bounded memory, and an operator-provided ACL file. It is optional and does not
alter the existing Looprd/PostgreSQL/network units unless its drop-in is installed.
Pub/Sub does not need Redis disk persistence. Durable application state remains SQL.

## Compatibility boundary

Existing v1 documents, scopes, event JSON, browser tickets, and synchronous handler
calls are unchanged. A new runtime accepts old documents. An old strict validator
will reject the new realtime field: deploy the new runtime before opting components
in. Remove realtime before downgrading the runtime; back up the checkpoint because
cached Redis specifications require the new runtime. Memory-only checkpoints retain
the legacy shape. No SQL
schema migration is introduced by the transport.

## Separate future contracts

Durable subscriptions need atomic SQL outbox writes, ordered event IDs, replay
retention/cursors, bounded catch-up, idempotent consumers, and authorization on both
live delivery and replay. OIDC needs issuer/JWKS/audience validation, principal
semantics, revocation/session policy, and membership/row checks. Named topics, Redis
presence, distributed quotas/jobs, typed service discovery and component calls
need their own explicit contracts. Do not advertise these as implemented syntax.

References: [Redis delivery and database scoping](https://redis.io/docs/latest/develop/pubsub/)
and [redis-py async lifecycle](https://redis.readthedocs.io/en/stable/examples/asyncio_examples.html).

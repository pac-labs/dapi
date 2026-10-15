# Durable resource events

Durability is optional and independent of Redis:

```yaml
requires: [events.transactional, events.replay]
realtime:
  backend: memory
  delivery: durable
  retentionSeconds: 86400
  maxEvents: 100000
  maxEventBytes: 65536
  maxReplayBytes: 1048576
```

It requires a component database and WebSocket resource declarations. For Redis,
use backend redis with the existing connectionSecret. SQL remains authoritative;
Redis notifications only wake consumers. Old descriptors and best-effort clients
keep their existing behavior. Upgrade the runtime before opting a descriptor in.

Native create/update/delete transactions atomically store event records. A SQL
clock lock orders committed sequences on SQLite and PostgreSQL. Failed validation,
constraint violations, event-size/capacity failures and transaction rollback publish
no durable event and commit no business write. Runtime event tables do not change
the application schema version/fingerprint and remain when durability is disabled.

Inline handlers can use `with ctx.transaction() as session:` followed by
`ctx.emit(session, {"resource": "items", "action": "updated", "data": {...}})`.
Mutations and emissions must use that same transaction; emit rejects unrelated or
unprepared sessions. Legacy `ctx.publish(event)` stays synchronous and best effort;
it cannot make arbitrary external/custom transactions durable. Trusted code remains
responsible for using the transaction helper for its changes.

`GET /_events/<resource>?after=<cursor>&limit=100` relative to the component prefix
returns events, a next cursor, startCursor and hasMore. The same authentication and
WebSocket resource scopes are required. Cursors bind to component, resource and
verified principal/key identity; editing or sharing them between identities fails.
The first request without after starts at the oldest retained event. Replay is
bounded by 100 events and the configured byte budget. Store a cursor only after
processing its event/page. Deduplicate by stable eventId after interrupted delivery.

For WebSocket resumption send `{"subscribe":"items","after":"<cursor>"}`.
The subscription acknowledgment reports the starting cursor, then catch-up events
are sent in order, each with its own cursor. Subscription without after starts live
at the current committed head. SQL polling fills gaps between catch-up/live delivery
and recovers missed Redis notifications. The outbox relay retries remote publication
after failures/restart. No exactly-once application processing guarantee is made.

Expired cursors return HTTP 410/resetRequired, or a WebSocket reset notice and restart
closure during ongoing delivery. Fetch current resource state and begin a fresh
subscription rather than silently skipping history. Retention is 60 seconds through
seven days. Capacity is 100 through one million records; a full retained log returns
503 and rolls back new durable writes until space expires. Per-event limits are
1 KiB through just under 1 MiB; replay pages are 1–8 MiB. Adjust these deliberately
for the host and payload sizes. Old/removed components retain their stored data;
cleanup resumes when a durable generation is running again.

`requires` declares implemented runtime capabilities. Unknown requirements reject
a candidate before activation and preserve its working generation. The protected
`GET /_looprd/capabilities` lists the supported contracts. This is feature negotiation,
not evidence that an external database/broker is currently reachable. Database and
secret initialization still validate actual deployment configuration.

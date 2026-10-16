# Generic platform extensions (`looprd/v1`)

`management.data` supports opt-in, management-key-only projected browsing,
constrained edits, transaction-bound component actions and append-only audit
receipts. [Operator administration](operator-administration.md) remains separate
from application user authentication and ownership policies.

The `reports.bugs` capability provides mandatory built-in bug-report submission
infrastructure for every component. New/updated clients must implement the
[application reporting contract](bug-reports.md); installed clients remain
compatible and need their own update to send reports.

All features are additive and opt-in. Existing public, bearer and database
authentication, best-effort clients, list arrays and unchanged model fingerprints
remain compatible. See [durable events](durable-events.md) for replay guarantees.

## Authentication and principals

No external provider or OIDC configuration is required. Applications own user
registration, credentials, recovery and device sessions. Looprd verifies access
and enforces generic authorization; it does not implement application accounts.

Optional `auth.mode: verified` declares `scopes`, optional `realm` (defaults to
component ID), and `verifier: {kind: python, source: ...}`. Trusted source defines
`async def verify(ctx)`, receiving bearer `credential`, `session_factory`,
`secret(name)` and `await ctx.run(function, ...)` for blocking work. It must validate
the proof against authoritative server state or cryptography. Return None for
invalid credentials or `{subject, scopes, sessionId?, expiresAt?, kind?}`.
Expiry uses Unix seconds; kind defaults to user and may be service for verified
machines. Exceptions, invalid identities, expired sessions and undeclared scopes
fail closed. Never promote a client-supplied ID or scopes without verification.
OIDC is only a possible future optional adapter.

Handlers receive `principal.id`, `kind`, `realm`, `subject`, `scopes`, `session_id`,
`expires_at`, `label`, and `permits(scopes)`. Verified IDs are SHA-256 of realm,
NUL, subject. Shared realms require agreement on the authoritative subject namespace.
Database keys preserve existing IDs. Shared bearer credentials are shared identities.
Management credentials remain separate and never become application principals.

Optional resource `policy.ownerField` injects the stable principal ID on create and
makes ownership immutable through native CRUD. `policy.membership` declares model,
subjectField, membershipField and recordField. All declared conditions must pass.
Private policies require database or verified authentication and apply before
pagination, to reads/writes, live events and replay. Current membership and current
records are checked; deletion uses the retained record snapshot. Custom private
events require `ctx.emit(session, event, record=record)` with a valid PK. Applications
must protect membership writes and authorize arbitrary trusted Python SQL themselves.
Credentials are revalidated on ongoing subscriptions, approximately once per second
while idle. Core-held proofs never appear in status responses.

## Data and services

Models support `unique: [[a,b]]`, `indexes: [{name, fields, unique?}]`, and field
`references: {model, field}` targeting declared generated integer PKs. Foreign-key
deletes restrict references; there are no automatic cascades. Structural changes
need explicit additive migrations and schemaVersion increases. SQLite table rebuilds
remain operator-managed. Failed candidate schemas retain the working generation.

SQLite uniqueness validation reads the database's index metadata, including implicit
indexes created by inline `UNIQUE` declarations on `VARCHAR(n)` columns. This avoids
false reload failures from DDL-text reflection. Partial and expression indexes do
not satisfy full-column/composite uniqueness; partial indexes do not satisfy a
declared full named index. No application-table rewrite or fingerprint change is
needed for this validation repair.

Resource `query.filters` maps scalar fields to allowed eq/ne/lt/le/gt/ge/in/prefix
operators; `query.sort` lists allowed non-null scalar fields. Requests use
`filter.name.prefix=report`, `filter.id.in=[1,2]`, `sort=-id`, and limit 1–100.
IN accepts at most 50 values; sorting allows three requested fields plus a PK tie-breaker.
Legacy lists remain arrays with offset. `pagination=cursor` returns
`{items,cursor,hasMore}`; reuse identical filters/sort. Signed cursors bind the
resource, query and principal. Paging is not a frozen database snapshot.
Read-only `projection: [id,name]` limits returned fields after authorization.

Resource `revisionField` identifies a required runtime-owned integer. Create starts
at 1; patch increments it. Get/create/patch return ETag. Patch/delete require
`If-Match: "<revision>"`: missing returns 428, stale returns 412. Trusted SQL writers
must preserve revision semantics themselves. CORS permits If-Match and exposes ETag.

Handlers can declare `contract: {name,version,request,response,serviceOnly,allowedCallers}`.
serviceOnly defaults true. Schemas support bounded JSON object/array/scalar/null
types, properties, required, additionalProperties, items, enum and size bounds;
external refs, regex and combinators are excluded. Input violations return 422;
invalid output returns 500. Callers declare dependencies with component, contract,
version and credentialSecret. Management issues scoped database keys with `service`
bound to the caller component. Verified machine adapters may supply the same identity.
`await ctx.call(component, contract, payload)` checks binding and normal target
authentication, scopes, policies and limits. Nesting is capped at eight. `GET
/_services` discovers only declared contracts, without secrets. Calls are local
to one runtime, not cross-host RPC or distributed transactions. Keep old versions
during rolling contract changes for in-flight callers. Source removal remains
authoritative, making dependent calls unavailable.

## Limits, jobs and coordination

Optional `limits` fields are requestsPerMinute, authAttemptsPerMinute, burstPerSecond,
maxInFlight, maxWebSockets and maxRows. Defaults add no per-component quotas.
Rate checks use authenticated principal IDs (peer addresses for public access);
pre-auth counters use the ASGI peer. Trust forwarded addresses only from known
proxies. Limits return 429/Retry-After. Native row quotas serialize SQL creates and
apply per model table, returning 507; trusted SQL and total disk use are outside them.
Memory counters cap identities at 4096 and reset on restart. Redis backend plus
connectionSecret shares atomic counters; failures return 503 for configured checks.
In-flight/socket caps remain process-local. See redis.md for optional counter ACLs.

`jobs` maps names to trusted async handle(ctx) sources. A database is required.
Options: execution (process default), timeoutSeconds 1–60, maxAttempts 1–10,
retrySeconds, intervalSeconds (minimum 60), secrets, expose and scopes.
Exposed POST `/_jobs/name` accepts `{payload,delaySeconds}` and returns 202/id;
GET `/_jobs/name/id` checks the enqueuer. Scoped authentication requires declared
job scopes. Payloads cap at 16 KiB, pending/running jobs at 1000. Persistent periodic
schedules start after one interval and coalesce missed intervals. SQL leases and
compare-and-swap completion reject stale workers. Retries are at least once;
applications must make effects idempotent. Finished history caps at one day/10,000
records. Failed payloads survive operator retries until retention expiry; successful
payloads are cleared. Management can list jobs and retry failures in Operations or
POST `/_looprd/components/component/jobs/id/retry`. Listings omit payloads.
Inline jobs are server service principals, not impersonated users; enqueued_by is
server-recorded metadata. API blocking leaves maintenance jobs running. Source
removal retires workers while retaining data.

An OS lock prevents two workers owning one data directory. PostgreSQL initialization
uses an advisory transaction lock; a persistent owner marker prevents different
component IDs sharing a SQL schema. Use separate databases or isolated schemas.
Run one ASGI worker: tickets, uploads, blocks and checkpoints are not active-active.

## Execution and operations

Python requires LOOPRD_ALLOW_PYTHON. Optional LOOPRD_TRUSTED_COMPONENTS narrows trust.
Handlers may select execution: process for bounded JSON computation. Workers have
no SQL/event/call helpers and receive explicit secrets through ctx. Host settings
LOOPRD_WORKER_MEMORY_MB (256 default) and LOOPRD_WORKER_CONCURRENCY (2 default)
bound memory and concurrency. Input/output, wall time and process creation are
bounded; Linux also limits CPU, file descriptors and file size. Cancellation kills
and reaps children. Inline work remains cooperative and must offload blocking calls.
Workers remain trusted code with the same OS user/filesystem: fault controls are
not a hostile-code security sandbox. Untrusted tenants need a separate boundary.

Protected GET `/_looprd/ops` and Carbon Operations show persisted minute request,
error and mean-latency aggregates, static management-write metadata and sanitized
rate/job/relay errors. Local SQLite retention caps at seven days/100,000 metric rows
and 10,000 audit entries. Responses show 1000 recent minute rows and 100 audit items;
displayed totals cover those rows. A bounded 512-entry queue reports drops.
No paths, query strings, headers, bodies, principals, filenames or capability URLs
are retained. This is best-effort operational history, not immutable compliance
audit or named-operator attribution. There is no external telemetry.

Back up the data directory and component databases together. Runtime-owned tables
are additive; older binaries ignore them. Rollback may require restoring the old
checkpoint and descriptors if new non-default fields are present. Keep the current
data backup and never run a descriptor requiring unsupported capabilities. Existing
Life/Social specifications are not automatically rewritten.

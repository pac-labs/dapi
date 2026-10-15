# Platform development passes

Accepted scope: implement the four passes in the 15 October proposal sequentially,
preserving existing descriptors, data, authentication and best-effort event clients.
The Redis runtime is already deployed and has passed an authenticated live fanout
probe; the proposal's uncertainty about production verification is now resolved.

## Pass 1: reliable events and capability negotiation

Optional descriptor requirements are checked before candidate activation. Durable
delivery is an explicit opt-in, backed by runtime-owned SQL tables outside the
application schema fingerprint. Native CRUD changes and their event records commit
atomically. Inline code gets a transaction-bound emit helper; legacy publish stays
best effort and must not be represented as a transactional publication.

Events have commit-ordered sequence IDs, bounded retention/capacity and authenticated
resource-scoped replay cursors. HTTP catch-up and WebSocket resumption share the same
authorization checks. Catch-up/live handoff must not lose events, even without Redis.
Retention gaps return an explicit reset requirement. Replays may repeat after client
failure, so clients deduplicate by event ID; exactly-once processing is not promised.

## Pass 2: verified principals and private access

Preserve public, bearer and database modes. Additional authentication is optional
and uses pluggable trusted server-side verification. A generic principal carries
a realm, subject, stable ID, verified scopes, optional session identity and expiry.
No client-supplied identity is promoted without verification. Applications own
registration, credentials, recovery and device sessions. No external identity
provider is required; OIDC is only a possible future adapter.

Opt-in owner/membership policies cover reads, writes, live events and replay.
Verification and current membership are rechecked on resumed/ongoing subscriptions.
Generic resource scopes alone do not authorize private records. Verifier failure,
expiry and malformed identities fail closed. Existing modes retain their behavior.

## Pass 3: reusable data and service contracts

Add explicit indexes/composite uniqueness/foreign keys, bounded declarative query
filters/sorting/cursors, revision checks and read-only projections. Preserve legacy
schema fingerprints for unchanged models; require explicit migrations for structural
changes. Add declared dependencies, versioned typed handler contracts, discovery and
scoped machine identities for internal calls. Management keys are not service keys.

## Pass 4: operations and coordination

Add bounded historical metrics/audit records without secrets or request/event payloads,
explicit rate limits and quotas, leased/retryable jobs, and coordination checks for
shared state. Keep the one-worker constraint until tickets, checkpoints, blocks,
uploads, reloads and migrations are coordinated together. Stronger execution controls
must distinguish fault/resource isolation from a security sandbox: trusted Python
must not become advertised as safe for untrusted tenants in a shared container.

## Acceptance for every pass

Generated schema and examples match runtime validation. Old specifications and
SQLite/PostgreSQL data keep working. Negative authorization tests, restart/reload
tests, failure/rollback tests and bounded-resource checks precede publication. New
production policies remain off until explicitly selected in local configuration
and component specifications. Each pass records exact implementation and limits;
unfinished guarantees must never be advertised as supported capabilities.

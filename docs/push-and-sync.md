# Background push and HTTP recovery

Looprd clients can synchronize without keeping a WebSocket open. HTTP replay is
authoritative; optional UnifiedPush/ntfy messages are only wake-up hints. Lost,
delayed or duplicated pushes do not change the recovery protocol. WebSockets
remain an optional fast path while an application is active.

## Specification

Use individual `database` or `verified` authentication for device registration:

```yaml
realtime:
  delivery: durable
  retentionSeconds: 86400
push:
  transport: unifiedpush
  scopes: [push:manage]
  deviceLifetimeSeconds: 86400
  maxDevicesPerPrincipal: 8
  maxDevices: 5000
  retrySeconds: 3600
```

Declare `push:manage` in `auth.scopes` and grant it through the application's
server-side verifier or a component API key. Durable events already require a
database and declared `websocket.resources`/permissions; those describe event
subscriptions even when no client opens a socket. Public/bearer components retain
existing HTTP replay and gain snapshots, but cannot register personal devices.
Omitting `push` preserves existing behavior. Optional capabilities are
`push.unifiedpush` and `events.snapshot`. See the [complete example](../examples/push-sync.yaml).

## Register a device

1. The application obtains an ntfy endpoint from its UnifiedPush distributor.
   A direct ntfy subscriber can also supply its own private topic endpoint.
2. With its usual application bearer credential, POST `/_push/devices`:
   `{"installationId":"<persistent random installation identifier>","endpoint":"https://push.example.com/up..."}`.
   Do not supply a user ID. Looprd binds the device to the verified principal.
3. The 202 response contains `id`, `status: pending`, `expiresAt` and
   `verificationExpiresAt`. The challenge is NOT returned by HTTP.
4. Receive the binary UTF-8 JSON push payload:
   `{"apiVersion":"looprd-push/v1","type":"verify","deviceId":"...","challenge":"..."}`.
5. POST `/_push/devices/<id>/verify` with `{"challenge":"..."}` using the same
   principal. The device becomes active. Pending registrations expire after ten minutes.

GET `/_push/devices` lists only the caller's device metadata, never endpoints or
challenges. DELETE `/_push/devices/<id>` unregisters an owned installation. Repeat
registration with the same installation/verified endpoint renews its lifetime
without another challenge. A changed endpoint requires new proof. Renew before
expiry, and unregister on logout. Registration expires no later than the identity
expiry supplied by authentication. Credentials are not persisted with devices.

Endpoint URLs are capability secrets: protect backups and avoid endpoint/challenge
logging on the client and proxy. Endpoints must be single-topic URLs on the
operator-configured public provider, not arbitrary user-selected servers. Another
installation cannot claim an already registered endpoint. Existing component
rate/body limits and registration quotas apply.

## Application service layer

Trusted **inline** Python handlers and inline jobs receive `ctx.notify` and
`ctx.revoke_push`; they are `None` unless push is enabled. Process handlers remain
isolated computation and do not receive database/push helpers.

```python
async def handle(ctx):
    with ctx.transaction() as session:
        # Perform application-authorized data changes with this same session.
        # Resolve recipients from trusted server-side account/membership data.
        ctx.emit(session, {
            "resource": "messages", "action": "created", "id": record_id,
            "data": projected_record,
        }, policy_record)
        ctx.notify(session, recipient_principal_id)
    return {"accepted": True}
```

`recipient_principal_id` is Looprd's canonical `Principal.id`, not an unverified
client user ID or arbitrary account name. For verified identities it is the stable
realm/subject hash already used by ownership policies. Social owns recipient
selection, account recovery, sessions, unread counts and permission checks.
Looprd never automatically broadcasts data changes to every registered device.

Queue writes commit or roll back with the application transaction. Each active
device receives only `{"apiVersion":"looprd-push/v1","type":"sync"}`: no text,
record, recipient, resource name or login token. One row per device coalesces hints.
Delivery retries transient/network failures with exponential delays, at most eight
attempts and the declared retry lifetime; 404/410 remove invalid endpoints. Other
4xx responses stop retrying that job; a later application hint can queue it again.
Success means the provider accepted the hint, not that a device processed it.

Call `ctx.revoke_push(session, principal_id)` during application-wide logout/account
revocation. Database-key revocation is checked before delivery. A generic verified
session cannot be revalidated in the background without its credential: Looprd
deliberately does not save that credential, so applications must wire revocation
and renew registration after session changes. API reads always reauthenticate.

## HTTP recovery

For each declared durable resource:

1. Without a saved replay cursor, GET `/_sync/<resource>?limit=100`.
2. Stage `items`, each `{id, data}`, in a fresh local snapshot. Follow `next` using
   `?cursor=<next>` until `complete: true`. The returned `cursor` is the same
   before-snapshot replay baseline on every page. Do not advance durable local
   progress from an incomplete snapshot.
3. Atomically install the staged snapshot and baseline in local storage.
4. GET `/_events/<resource>?after=<cursor>&limit=100`. Apply events in order,
   deduplicate by `eventId`, and handle `action: deleted` by removing `id`.
   Apply each page and save its cursor in one local transaction, including empty
   pages, then continue while `hasMore` is true.
5. Repeat catch-up on push, startup, resume and connectivity restoration. When
   push is unavailable, use application-appropriate periodic catch-up while active.

Snapshot keyset pages avoid offset gaps when records are deleted; a captured
upper primary-key fence plus replay baseline closes concurrent-write races.
Snapshot cursors are signed and bound to principal, resource and generation;
they expire after five minutes. HTTP 410/resetRequired means discard staging and
start a fresh snapshot. HTTP replay 410 means replace local state from a snapshot.
401 requires session renewal; 403 requires permission handling, not blind retries.
The resource must expose `list`, and the caller needs both list and event scopes.
Snapshots/replay enforce row policies and declared projections. Application code
must emit durable events for all changes using `ctx.transaction`/`ctx.emit`.
Direct SQL changes without events cannot be recovered by this protocol.

Membership/permission changes may remove visibility without producing a resource
deletion. Social must trigger full resynchronization when access changes, and
discard account-bound local state on sign-out/account switch. Snapshot/replay
transport cannot infer domain membership changes or erase already downloaded data.

### JavaScript/Tauri helper

Copy/import [clients/looprd-sync.mjs](../clients/looprd-sync.mjs). `recoverResource`
supports HTTP-only bootstrap/catch-up, bounded retries for network/429/5xx,
timeouts/cancellation, expired-snapshot restarts and retention-gap recovery:

```javascript
await recoverResource({
  baseUrl: componentApiPrefix, resource: "messages",
  authorization: async () => "Bearer " + await currentSessionToken(),
  storage: accountBoundStorageAdapter,
  signal: abortController.signal,
});
```

The adapter supplies async `getCursor`, `beginSnapshot`, `appendSnapshot(items)`,
`commitSnapshot(cursor)`, `abortSnapshot` and `applyEvents(events, cursor)` methods.
The last two commit methods MUST atomically persist data and cursor; implement
idempotent event application. Serialize calls per account/resource (coalesce push
and resume triggers). The helper does not choose storage, start background loops,
register native distributors or display notifications. Social must add its native
UnifiedPush connector and trigger this helper. Web Push/APNS/FCM adapters for
Social are separate integrations, not advertised by this first service layer.

## Provider and container deployment

Host-only settings, never application YAML:

```dotenv
LOOPRD_PUSH_PUBLIC_URL=https://push.example.com
LOOPRD_PUSH_DELIVERY_URL=http://looprd-ntfy:8080
# Optional, if publishing requires a token:
# LOOPRD_PUSH_TOKEN_SECRET=push/publish-token
```

Public URL must be HTTPS. Optional internal delivery uses the trusted operator
origin; only the validated topic path is forwarded. Redirects and environment
proxies are disabled. Credentials come from mounted local secret references.
Without a configured provider the rest of the API works, while registration
returns 503. Endpoint/provider URLs are omitted from generated OpenAPI and status.

Quadlet examples: `deploy/quadlet/looprd-ntfy.container`, its separate data volume
and push network, and the optional runtime drop-in. Copy
`deploy/ntfy/server.yml.example` into private node configuration. The default host
binding is localhost:8081; privately bind the appropriate LAN address when Traefik
runs elsewhere. Route a dedicated HTTPS hostname using
`deploy/traefik/push.yml.example`; preserve its entire path and do not strip prefixes.
Retain the existing Looprd networks. This companion does not share application DBs.

Provision an ntfy distributor account and explicit read rights to its topics.
UnifiedPush requires anonymous publishing to its unpredictable `up*` topics;
grant write-only access, never anonymous read access:

```sh
podman exec -it looprd-ntfy ntfy user add distributor
podman exec looprd-ntfy ntfy access distributor 'up*' read-only
podman exec looprd-ntfy ntfy access everyone 'up*' write-only
```

The account password is entered interactively. For separate untrusted users,
provision per-topic read rights or distinct providers/accounts; the sample's one
distributor account can read all `up*` hints on that server. Follow ntfy's
[UnifiedPush access guidance](https://docs.ntfy.sh/config/#example-unifiedpush)
and [container instructions](https://docs.ntfy.sh/install/#docker).
ntfy's own iOS/Web Push facilities do not automatically integrate Social clients.

## Operations and rollback

GET `/_looprd/components/<component>/push` requires the management key and returns
only device/queued/failed counts, configured state and safe delivery error codes.
Authenticated component status includes push enablement/configuration/error.
Failed jobs remain visible until retry lifetime expiry. Back up component data and
the separate ntfy volume; endpoints in backups are secrets. Removal, blocking or
spec opt-out prevents new delivery, retaining data. Existing requests may finish.

Runtime-owned versioned device/queue tables do not alter application schema
fingerprints. To downgrade, remove `push` and new `requires` entries first and use
an older runtime; stored tables are retained. Recovery endpoints/helpers need a
compatible runtime. No account system, external identity provider or new mandatory
container is introduced.

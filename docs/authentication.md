# Database-backed API keys

Set `auth.mode: database` to use individually revocable API keys in a component's
SQLite or PostgreSQL database. See [the complete example](../examples/database-auth.yaml).
Looprd creates its internal `_looprd_api_keys` table automatically; application models
and migrations must not define or modify tables beginning with `_looprd_`.

Declare the available scopes in `auth.scopes`, then require scopes for every exposed
CRUD operation, custom handler, and WebSocket resource. Every listed scope is required
(all-of). Scope names are exact, case-sensitive strings; there are no wildcards or implicit
grants. Missing, empty, duplicate, or unknown permission requirements reject the specification.

```yaml
auth:
  mode: database
  scopes: [items:read, items:write, events:read]
resources:
  items:
    model: Item
    path: /items
    operations: [list, get, create]
    permissions:
      list: [items:read]
      get: [items:read]
      create: [items:write]
websocket:
  path: /events
  resources: [items]
  permissions:
    items: [events:read]
```

Custom handlers declare `scopes: [items:read]`. Python handlers receive `ctx.principal`
with `id`, `label`, and immutable `scopes`, and can use it for application-specific checks.
An event subscription reveals the resource's full event payload; grant its scope only to
clients allowed to see all those fields. Scopes do not implement per-row ownership.

## Provision and revoke keys

Management requires the generated management key or mounted `admin-token`.
The [management panel guide](management-panel.md) covers first-start output and recovery.
These routes are excluded from the normal component Traefik router; an optional management
router enables HTTPS panel access. Otherwise use localhost or a private SSH tunnel.
Never send the management key to a component client.

| Request | Behavior |
|---|---|
| `POST /_looprd/components/<id>/keys` | Create a key; return `apiKey` once, with metadata (201) |
| `GET /_looprd/components/<id>/keys` | List key metadata, including expired/revoked keys |
| `DELETE /_looprd/components/<id>/keys/<key-id>` | Revoke a key (204); unknown key returns 404 |

```sh
# Set LOOPRD_ADMIN_KEY to the key printed in your container terminal.
curl -H "Authorization: Bearer $LOOPRD_ADMIN_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"label":"dashboard","scopes":["items:read","events:read"],"expiresInSeconds":2592000}' \
  http://localhost:8080/_looprd/components/demo/keys
```

The request requires `label` (1–100 characters) and a nonempty subset of declared scopes.
`expiresInSeconds` defaults to 30 days, accepts 1 second through 365 days, or `null` for
an explicitly non-expiring key. Responses use Unix UTC timestamps: `createdAt`,
`expiresAt`, and `revokedAt`; `id` is the identifier for revocation.
Save the returned `apiKey` securely. Listing never returns keys or their hashes.

Clients send `Authorization: Bearer <apiKey>` over HTTPS to
`https://api.example.com/looprd/demo/v1/items`. Invalid, expired, revoked, or another
component's keys return 401; a valid key missing required scopes returns 403. Any valid
component key can read its generated OpenAPI document, which includes
`x-looprd-required-scopes` per operation.

Browser clients request `POST /_ws-ticket` using their key, then connect with the returned
single-use, 30-second ticket. Ticket issuance requires access to at least one WebSocket
resource. Tickets bind to the original key and generation; revoked or expired keys cannot
redeem outstanding tickets. Subscription requests check resource scopes, and unauthorized
subscriptions return `{"error":"insufficient scope"}` without subscribing.
Open sockets recheck their key before sending events and while idle, about once a second
plus database latency. Revocation or expiry closes them with code 1008. Already completed
operations are unaffected; authentication does not roll back in-flight requests.

Keys use a random 256-bit secret; only a SHA-256 digest is persisted. Key metadata and
hashes remain sensitive database contents: include them in protected backups. Keys and
revocations survive hot reload, restart, removal, and re-addition with retained data.
Enabling database authentication on an existing component adds the internal table without
changing its application schema version. Reverting to public/shared bearer mode retains
the table; returning to database mode restores its stored keys, including revocations.

The other modes remain available: `public` and `bearer` with a local `secretRef`.
They grant component-wide access and cannot declare scopes or operation permissions.
The GitHub retrieval token is separate from both component keys and the management token.
User accounts, passwords, login/session endpoints, roles, and row-level policies are future
features; declaring a user model alone does not enable them.

Design reference: `DESIGN.adoc`, “Authentication and management”. Security guidance:
[OWASP REST Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/REST_Security_Cheat_Sheet.html).

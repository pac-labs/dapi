# Looprd specification contract

`looprd.yaml` is self-contained: it carries identity/version, authentication policy, database
models, API operations, WebSocket subscriptions, migrations, and optional custom Python source.
There are no handler files or component images to chase. Secrets remain local references.
See [the complete example](../examples/looprd.yaml) and [generated JSON schemas](../schemas).

Validate documents before committing them:

```sh
python -m looprd validate looprd.yaml
python -m looprd validate --sources sources.yaml
```

IDs match `[a-z][a-z0-9-]{0,47}`. Table and field names match `[A-Za-z][A-Za-z0-9_]{0,63}`.
Unknown fields, duplicate YAML keys, invalid references, and overlapping resource routes are rejected.
Each document is limited to 1 MiB. One source entry registers one component.
Paths are literal slash-separated names; CRUD item routes are generated automatically.

## Models and CRUD

`database.backend` is `sqlite` or `postgresql`. SQLite paths derive from the component ID.
PostgreSQL additionally requires `database.connectionSecret`, naming a local file containing
the component's dedicated PostgreSQL connection URL. Provision that database and role first.
Looprd never provisions database server accounts or deletes their data.

Models declare fields of type `integer`, `string`, `float`, `boolean`, `datetime`, or `json`.
Each has one generated integer primary key. Strings require `maxLength`.
`required` and `unique` default to false. Optional fields are nullable.
Generated primary keys cannot be supplied by clients. Datetimes use ISO-8601 JSON strings.

Resources explicitly expose a model at a collection path and choose from:

| Operation | HTTP request | Success |
|---|---|---|
| `list` | GET collection, optional `limit` and `offset` | 200, array |
| `get` | GET collection/id | 200, record |
| `create` | POST collection, JSON object | 201, record |
| `update` | PATCH collection/id, supplied fields only | 200, record |
| `delete` | DELETE collection/id | 204 |

Lists default to 50 records and cap at 100. Missing items return 404, invalid fields 422,
and constraint conflicts 409. Fields are strictly typed; numeric strings are not integers.
Each operation uses an independent SQLAlchemy transaction. Writes emit events only after commit.
All routes inherit the component auth policy. Public/shared bearer modes grant component-wide access.
Database mode uses individually revocable API keys with explicit per-operation scopes.
See [authentication and key management](authentication.md) and the
[complete database-auth example](../examples/database-auth.yaml).
User accounts and per-record permissions are not implemented.
OpenAPI is available at the authenticated `/looprd/<component>/v1/openapi.json` route.

## Custom routes in the same file

A `handlers` entry can return constant JSON with `kind: response`, `path`, `method`,
`status` (default 200), and `response`. With `kind: python`, `source` contains Python defining
`async def handle(ctx)`. See [the inline handler example](../examples/python-handler.yaml).
Inline Python is disabled by default and requires `LOOPRD_ALLOW_PYTHON=true`.

The context exposes `request`, parsed JSON `body`, `specification`, `session_factory` (or None),
`secret(reference)`, `publish(event)`, and authenticated `principal`. The handler returns JSON-serializable data.
Handlers validate their own custom request shape. The declared `status` applies to their response.
Use a thread for blocking database/CPU work and short-lived sessions with the session factory.
If a custom handler publishes events, it must do so on the event loop after its transaction commits.

Python source is trusted application code, with the same filesystem/network permissions as Looprd.
It is not sandboxed. All dependencies must exist in the runtime image; spec loading does not install packages.
Top-level handler code runs during candidate compilation, so it must have no external side effects.
Do not start unmanaged background tasks or retain globals outside the generation.
For declarative-only hosts, leave Python disabled.

## WebSockets

Declare `websocket.path` and its allowed `resources`. Connect at the component prefix plus that path.
Non-browser clients can send the bearer Authorization header during the handshake.
Browsers POST to `/_ws-ticket` under the component prefix with the bearer header, then connect
using `?ticket=<returned-ticket>`. Tickets expire after 30 seconds and can be used once.
Configure `LOOPRD_ORIGINS` with the permitted browser origins.

Send `{"subscribe":"scans"}` and wait for `{"subscribed":"scans"}`.
Events contain `component`, `resource`, `action`, `id`, and `data`.
Actions are `created`, `updated`, or `deleted`; deletion sends `data: null`.
Events are best effort. After a disconnect, fetch current state and resubscribe.
Slow consumers are disconnected with 1013. Reload/removal drains old connections and closes them with 1012.

## Schema changes

The initial schema is created from the models. A changed model requires an increased
`database.schemaVersion` and ordered migrations for every version from 2 through the target.
The complete migration history stays inside the specification:

```yaml
database:
  backend: sqlite
  schemaVersion: 2
  migrations:
    - version: 2
      statements:
        - ALTER TABLE "Scan" ADD COLUMN note VARCHAR(200)
```

Also declare the matching optional string field `note` with `maxLength: 200` under the model.
Quote mixed-case table/field identifiers in SQL, as PostgreSQL treats unquoted identifiers as lowercase.
On a new database, Looprd creates the latest declared model directly. On an existing one,
it runs only pending versions in a transaction. Automatic SQL is restricted to additive
`ALTER TABLE ... ADD COLUMN`, `CREATE TABLE`, and `CREATE [UNIQUE] INDEX` statements.
Use one statement per entry; scripts, comments, destructive changes, and schema downgrades are rejected.
Looprd checks column names/types/nullability before activation. It does not synthesize migrations.

Back up first and keep additions compatible with old requests draining during reload.
Application reload rollback does not reverse a committed migration. Maintenance involving renames,
deletions, backend changes, or database moves must be performed separately and deliberately.
Database identity stays fixed across reload/removal/re-addition so accidental backend changes cannot hide data.

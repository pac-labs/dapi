# Operator data administration

Looprd supplies generic operator infrastructure. Components own their roles,
balances, validation and other domain behavior. Nothing is exposed by default:
the component must declare `management` in its `looprd.yaml`.

The management key grants component-wide operator access to explicitly declared
records/fields, independently of application user scopes and ownership policies.
Application keys, verified users and diagnostic capability links never grant
operator access. Public API blocking does not disable operator repairs.
The capability is `management.data`; declare it in `requires` when needed.

## Declaration

```yaml
management:
  resources:
    accounts:                       # an existing resource
      label: Accounts
      titleField: username
      fields:
        username: {label: Username, editable: true}
        role: {label: Role, editable: true, choices: [player, admin]}
        coins: {label: Coins, minimum: 0}
  actions:
    adjust-coins:
      label: Adjust coins
      resource: accounts
      inputs:
        amount: {label: Amount, type: integer, minimum: -1000000, maximum: 1000000}
      writes: {accounts: [coins]}
      source: |
        def handle(ctx):
            account = ctx.get('accounts', ctx.recordId)
            balance = account['coins'] + ctx.body['amount']
            if balance < 0:
                ctx.reject('insufficient_balance')
            ctx.update('accounts', ctx.recordId, {'coins': balance})
```

See [the complete account example](../examples/operator-accounts.yaml), including
a no-input administrator promotion action. These names have meaning only in the
example application; Looprd does not define administrator roles or game currencies.
Changing a role updates application data. The application remains responsible for
interpreting it and refreshing session permissions; it does not grant a Looprd
management key or automatically change database API-key scopes. The complete
example keeps the client API read-only while explicitly permitting operator edits.

Resource keys reference existing resources, not arbitrary tables. Only listed
fields are readable. `editable` defaults to false. Optional `label`, numeric
`minimum`/`maximum`, and primitive `choices` constrain the operator interface and
are enforced by the server. Underlying strict model types, nullability, lengths,
uniqueness and foreign keys also apply. Generated IDs, ownership fields and
revision fields cannot be edited, including through another resource for the
same model. IDs are returned separately for selecting records.

Direct edits intentionally do not execute application HTTP handlers. Authors
must expose only fields whose domain rules are satisfied by declared/model
validation. Use a management action for operations requiring business rules or
coordinated changes. Never expose password hashes, credentials or internal fields
just to make the administration screen more complete.

## Trusted application actions

Actions require `LOOPRD_ALLOW_PYTHON=true` and the existing trusted-component
policy. Their inline source defines synchronous `def handle(ctx)` and returns
None. Inputs are declared scalar `string`, `integer`, `float` or `boolean` fields,
with optional bounds, choices, labels, required=false and maxLength (default 200,
maximum 2,000). Unknown input fields and incorrect types are rejected.

The context supplies:

| Member | Purpose |
|---|---|
| `ctx.recordId` | Selected target's generated integer ID |
| `ctx.body` | Validated action inputs |
| `ctx.reason` | Operator explanation |
| `ctx.principal` | Generic `kind="operator"` identity, never an impersonated user |
| `ctx.get(resource, id)` | Read declared fields within the transaction |
| `ctx.update(resource, id, values)` | Validate/update only fields in the action's `writes` grants |
| `ctx.reject("safe_reason_code")` | Reject with 422 and roll back all changes |

Actions can coordinate updates across declared resources, with a maximum of 50
update calls per transaction. A field can be action-writable while remaining
read-only in the ordinary editor. No create/delete/arbitrary SQL operation,
database session/engine, secret accessor, commit method or external API helper is
part of this contract. Handlers must return promptly, use the provided helpers,
and avoid external side effects, background work, direct database connections or
commits. Helper execution has a deadline and database waits are bounded, but
trusted in-process Python cannot be forcibly stopped safely if it loops without
calling helpers. This is not a sandbox for untrusted repositories.

Updates increment existing model revision fields and emit normal resource events
after commit; durable events and audit are stored in the same transaction.
Operator event payloads honor the resource's projection; internal full-record
snapshots remain available to permission checks without exposing hidden columns.
Failed actions roll back records, events and audit. Best-effort notification
failure after commit is reported as a warning, never falsely as a rollback.

## Operator workflow

Log in and open **Data & actions**, choose a service/resource, search visible
fields or an exact record ID, and page through records (25/page). Open a record
for typed editing or a named management action. Provide a reason, review the
target and proposed changes/inputs, then confirm. The editor preserves drafts
during workspace navigation and requires confirmation before discarding them.
The panel does not store application records in localStorage.

The server checks both the active specification generation and a record ETag.
ETags use a server-only HMAC key and cannot be used to guess hidden field values.
They change when the generation is rebuilt or the server restarts; already
committed requests still return their persisted idempotent receipt.
Stale records return 412; changed specifications return 409. Close/reopen the
record or reload management options before creating a new intent. Do not blindly
retry a conflicting balance change. The UI reuses the same operation ID when
retrying the same confirmed request, preventing duplicate coin adjustments if a
response is lost. Changed input with an already used operation ID is rejected.

Audit entries are append-only metadata: timestamp, shared-management-key actor,
operation, target, reason, specification generation, receipt and changed field
names. They do not store before/after record values or credentials. A shared key
cannot identify which human operator performed a change. Audit history is paged
(50/page) and persists in `_looprd_operator_audit_v1` in the component database,
outside its application fingerprint/version. It is not automatically pruned,
because entries also prevent replay of prior operation IDs. Include it in backups
and consider receipt retention when archiving databases.

Removing the declaration disables browsing/writes and retains data/audit. Adding
it back restores audit access. Enabling management initializes the separate
runtime-owned table without an application migration. Existing specifications,
authentication modes, public API semantics and row policies remain unchanged.
Downgrading requires restoring a compatible cached specification or removing new
management fields/capability requirements; keep a matching data backup.

## Machine API

All paths require the management bearer key and return no-store JSON. Component
and action source code are omitted from descriptors.

| Method | Path | Purpose |
|---|---|---|
| GET | `/_looprd/data` | Active services and whether administration is enabled |
| GET | `/_looprd/data/{component}` | Active generation, field/input types, labels and grants |
| GET | `/_looprd/data/{component}/resources/{resource}?q=&offset=0` | Projected records and nextOffset |
| GET | `/_looprd/data/{component}/resources/{resource}/{id}` | Values, ID and ETag |
| PATCH | Same record path | Direct permitted field changes |
| POST | Record path + `/actions/{action}` | Run the named action |
| GET | `/_looprd/data/{component}/audit?offset=0` | Audit entries and nextOffset |

The [generated request schema](../schemas/operator-write.schema.json) describes
the common envelope; values are validated against the active component descriptor.
PATCH/POST bodies are limited to 16 KiB and require:

```json
{
  "generation": "<64-character digest from the descriptor>",
  "etag": "<64-character ETag from the record>",
  "operationId": "<new UUID for this operator intent>",
  "reason": "Support ticket or explanation",
  "values": {"amount": 25}
}
```

For PATCH, values are changed record fields; for actions, values are the declared
inputs (an empty object is valid for no-input actions). The receipt contains
operationId, duplicate and changedRecords. Retrying the exact same successful
request returns its receipt without repeating writes. Responses include 401 for
invalid management authentication, 403 for disabled grants, 404 for missing
records/components, 409 for generation/receipt/constraint conflicts, 412 for stale
records, 413 for size, 422 for invalid input, and 503 for unavailable storage.
Action exceptions return a sanitized error and appear in diagnostic history.

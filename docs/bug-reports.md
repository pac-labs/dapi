# Central application bug reports

One persistent inbox serves all applications. No new container or application
database migration is required. Every active component gets the built-in route.
Its capability is `reports.bugs`; include this in `requires` to reject older runtimes.
The standalone [JSON payload schema](../schemas/bugreport.schema.json) is generated
from the same contract as the endpoint and OpenAPI definition.

## Required client integration

New and updated Looprd-enabled applications **must** provide a way to submit a
bug report with the user's consent. Include application version, a useful message,
a stable error code where available, stack trace and minimal diagnostic context.
Show submission success/failure without recursively reporting reporting failures.
Existing binaries remain compatible but need an application update to implement
submission: a YAML declaration cannot add client code. No automatic telemetry is added.

Discover the payload in the component's generated `openapi.json`, including
`x-looprd-client-requirements.bugReports`. Submit:

```http
POST /looprd/my-app/v1/_bugreports
Content-Type: application/json
Authorization: Bearer <existing application credential>
```

```json
{
  "apiVersion": "looprd-bugreport/v1",
  "appVersion": "1.2.3",
  "category": "network",
  "errorCode": "CONNECT_FAILED",
  "message": "Could not reconnect after waking from sleep",
  "stackTrace": "at reconnect (client.js:42:7)",
  "platform": "Windows",
  "diagnostics": {"retryCount": 3, "connectionState": "disconnected"}
}
```

Both `/looprd` and `/dapi` work. Authentication, CORS and operator blocking follow
the component. Public components accept public reports; bearer, database and
verified components require a valid principal. Any valid principal may submit
without additional scopes, but gains no read access. Component identity comes
from the route; client component IDs, user IDs and deduplication hashes are rejected.
Public components using application-specific sessions in custom handlers still
expose this infrastructure endpoint publicly. Never send the management key.

Required fields: `apiVersion`, `appVersion`, `message`. Category defaults to
`application`; the other fields are optional. Limits: 64 KiB/request; version/error
code 100 characters; category 80; message 4,000; trace 24,000; platform 200.
Diagnostics supports 32 scalar entries, keys up to 100 characters and strings up
to 1,000 characters. Nested objects, non-finite numbers and unknown fields are rejected.

Clients **must omit credentials, personal data, request bodies and unrelated logs**.
Server redaction additionally removes common credential values, authorization
values and URL credentials/query strings/fragments. Arbitrary free text cannot be
guaranteed secret-free. No headers, IPs, principal IDs or application rows are
automatically attached.

Success is `202 Accepted`:

```json
{"id": "<server issue ID>", "deduplicated": false, "occurrences": 1}
```

Failures: 401 authentication, 413 size, 415 content type, 422 invalid payload,
429 rate limit (`Retry-After: 60`), 503 blocked component or unavailable storage.
Do not retry continuously or queue unlimited reports. This is best effort;
retrying an accepted request may increase occurrences because there is no
per-submission idempotency key. Clients must not recursively report these failures.

## Grouping and retention

The server hashes component, category, error code, sanitized message and stack.
Stack line coordinates, UUIDs and hexadecimal addresses are normalized. App
version, platform and diagnostics do not split issues. Other differences in
messages/stacks can still create separate issues: grouping is deterministic,
not semantic. Components never share an issue.

Duplicates increase occurrences and update the latest sample/last seen. First
seen is preserved; a repeat reopens a resolved issue. Only one latest sample is
kept. The global maximum is **100 distinct issues**, including resolved issues.
A new issue evicts the oldest resolved issue first, then the least recently seen
open issue. Duplicates work at capacity. This is an inbox, not archival storage.

Additional limits are 30 requests/minute/component and 120/minute/runtime,
including rejected payloads after authentication. These bounded process-local
limits reset at restart. Public submission remains susceptible to spam; use
component authentication or proxy limits when stronger abuse control is needed.

## Protected review

Log in to the management panel and select **Bug reports**. Filter by component,
state or text, inspect the latest sample, resolve/reopen issues or confirm deletion.
Scrolling stays inside the report panel. Machine clients can use the same APIs,
all requiring the management bearer key:

| Method | Endpoint | Result |
|---|---|---|
| GET | `/_looprd/bugreports` | Versioned inbox, limit and up to 100 summaries |
| GET | `/_looprd/bugreports/{id}` | Sanitized latest sample, timestamps and count |
| PATCH | `/_looprd/bugreports/{id}` | `{"state":"resolved"}` or `{"state":"open"}` |
| DELETE | `/_looprd/bugreports/{id}` | Permanent deletion, 204 |

Timestamps are Unix milliseconds. Summaries omit stack traces/diagnostics. All
responses are no-store. Diagnostic capability links never include reports.
The existing public Traefik component router already carries submissions;
management routes retain the existing management-access policy.

Runtime-owned `bugreports.sqlite3` lives in the persistent data volume; include it
in backups. Removal of a component, reload and restart retain its issues. A
downgrade retains the file but removes the feature until upgraded again. Existing
application schemas/migrations are unchanged. `/_bugreports` is reserved: an
application already using that custom route must rename it before adopting this runtime.

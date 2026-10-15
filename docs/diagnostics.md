# Agent diagnostics

Use **Diagnostics** in the management panel to create a read-only JSON URL for a
model or remote agent. Select all components or one component and a lifetime:
default one hour, maximum twelve hours. Anyone holding the link can read its
diagnostics until expiry or revocation. Copy the newly created URL; it is shown
only once. Looprd persists token hashes, expiry and revocation locally. No extra
container, external identity provider or `looprd.yaml` change is required.

Responses use `apiVersion: looprd-diagnostics/v1`. They contain generation time,
scope, component health, current failures and failure history. Times are Unix
seconds. `lastWorkingRetained` identifies a rejected reload with a running older
generation. Historical failures remain after recovery; `currentFailure` reflects
the current component error state. Scoped links exclude other components and
global source failures. Component names, releases and failure metadata are visible.

Failures have stable `code`, `stage`, `exceptionType`, `message`, `action` and
`details` fields. Migration failures identify previous/target schema versions,
migration version, zero-based statement index and specification field path.
Recognized database codes/reasons explain common failures without exposing SQL,
connection strings, raw database messages or application records. Validation errors
show structural field paths and types; arbitrary names/extra keys become `*`.
Unknown exceptions receive generic guidance. No tracebacks or automatic repairs
are exposed. Existing management status error strings remain compatible.

History lives in `diagnostics.sqlite3` in the data directory for seven days,
bounded to 500 distinct records. Repeats update last-seen time. Reads return the
latest 100 records in scope. History writes are best effort; storage failures do
not replace the original error. Diagnostic reads return 503 if storage fails.

| Method and path | Purpose |
| --- | --- |
| `GET /_looprd/diagnostics` | Management: safe snapshot and active links |
| `POST /_looprd/diagnostics` | Management: create using `{ "component": null, "expiresInSeconds": 3600 }` |
| `DELETE /_looprd/diagnostics/{id}` | Management: revoke a link |
| `GET /_looprd/diagnostic/{token}` | Read JSON with the capability URL alone |
| `GET /_looprd/diagnostics.openapi.json` | Public static OpenAPI reader contract and response schemas |

Management calls require the existing management bearer key. Creation returns
`id`, `component`, `expiresAt` and a relative `path`; prepend the instance origin.
At most 100 active links are allowed. Unknown/revoked/expired URLs return 404.
Capability tokens cannot authenticate management or component APIs. Responses use
`Cache-Control: no-store` and `Referrer-Policy: no-referrer`.

Serve links over HTTPS and treat them as secrets. Configure external proxy access
logging to omit/redact diagnostic paths. Do not publish links in issues or docs.
The supplied Looprd launcher disables access logging. Links cannot browse files or
read application rows; those APIs retain their own authentication and permissions.
Repository URLs, browser origins, credentials and handler source are excluded.

Back up the data directory to preserve grants and history. Restoring an older
backup can restore an old grant until its original expiry. Removing the diagnostic
feature discards only its history/grants; application databases are unaffected.

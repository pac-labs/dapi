# Temporary file transfers

Open **Files** in the authenticated management panel, enter a filename, and select
**Request upload URL**. Copy the generated Bash/POSIX terminal command. Replace its
local path if necessary, then run it on the machine holding the file:

```sh
curl --fail-with-body --upload-file './report.txt' 'https://api.example.com/_looprd/files/upload/<invitation>'
```

The upload requires the single-use invitation, not the management API key. It is a
raw PUT of one file: use `--upload-file`, not `--form` or a multipart collection.
The panel refreshes every ten seconds while visible with Auto-refresh enabled;
**Refresh files** also checks immediately. After completion it shows the download
URL and a **Copy download URL** button. The terminal response includes the same
download path relative to the server's origin. The upload command is shown once
and kept only in the current page; reload preserves the invitation but cannot
recover its upload secret. Delete it and request another if the command is lost.

Anyone with the unguessable download link can download without signing in. There
is no anonymous file list, directory browser, or filename lookup. Downloads use
attachment/octet-stream headers rather than displaying uploaded HTML on the API
origin. Upload and download identifiers are independent. The panel can list and
delete files or unused invitations only with its management credential. Deleting
does not require waiting for expiry; it makes the download unavailable immediately.
Deletion during an upload is rejected; retry once it completes or fails.

Files and invitations expire **12 hours after the invitation is created**. Uploading
does not extend that deadline. Expired downloads and uploads are immediately denied;
expired bytes/metadata are swept every 30 seconds and on restart. When the host is
stopped, physical cleanup resumes on the next start. Separate backups may retain
copies according to their own retention policy.

Limits default to 100 MiB per file, 1 GiB total storage reservation, 128 live file/
invitation records, and five minutes per upload. An in-progress upload reserves its
maximum file size against the quota. Completed files consume their actual size.
Oversized or interrupted accepted uploads discard partial data and consume the
invitation. A quota rejection before acceptance leaves it usable for retry.

Host settings `LOOPRD_FILE_MAX_BYTES` and `LOOPRD_FILE_QUOTA_BYTES` override size and
quota in bytes. Bytes and SQLAlchemy SQLite metadata persist in `files/` beneath
`LOOPRD_DATA`. Component removal or blocking does not affect this built-in service.

The shipped public Traefik rule includes only the upload/download prefixes. Keep
`/_looprd/files` listing/invitation creation and deletion behind management auth;
the optional management router exposes the authenticated panel/API. Existing
whole-domain routers forwarding all requests already cover these paths. Preserve
prefixes and allow PUT. Ensure proxy upload limits and request timeouts accommodate
your chosen file size and the five-minute deadline. Capability paths must be omitted
from proxy access logs; Looprd disables its own HTTP access logs.

## API contract

| Method and path | Access | Result |
| --- | --- | --- |
| `POST /_looprd/files` | Management bearer | `{"filename":"report.txt"}` → 201 invitation, uploadUrl, expiry |
| `GET /_looprd/files` | Management bearer | Files/invitations, states, expiry, limits, completed downloadUrl |
| `DELETE /_looprd/files/{id}` | Management bearer | 204; revoke invitation/delete bytes |
| `PUT /_looprd/files/upload/{token}` | Single-use capability | Raw file bytes → 201 completed metadata/downloadUrl |
| `GET`/`HEAD /_looprd/files/download/{id}` | Unlisted capability | Attachment, HEAD, or byte range |

JSON URLs are relative to the current origin. Timestamps are Unix milliseconds.
States: pending, uploading, ready, failed. Unknown/expired public links return 404;
consumed upload URLs return 409; oversized files 413; multipart 415; capacity 507.
Expired records disappear from management rather than accumulating indefinitely.

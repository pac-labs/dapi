# Schedule and storage workspaces

Governing design: DESIGN.adoc / Purpose / Operator schedule and storage visibility.

Both workspaces require the management key and expose read-only JSON:

- `GET /_looprd/schedule`: periodic jobs, pending/running jobs and retries, source
  refresh, file expiry cleanup, telemetry flush and enabled delivery workers.
- `GET /_looprd/storage`: filesystem capacity, bounded Looprd-data accounting and
  optional companion-volume/memory measurements from the existing host sampler.

The panel retains Carbon navigation, phone selector and scrolling inside panels.
Visible views refresh with the existing ten-second Auto-refresh setting; both have
manual refresh. Storage scans are cached for 30 seconds.

## Schedule interpretation

Times are displayed in the browser's local timezone. Periodic next times come from
the SQL schedule; retries use queue due times. A running job shows its lease expiry,
not a predicted completion time. Due times are eligibility, not execution guarantees.
Cadence-only workers do not advertise an invented next timestamp. Discovery is
event-driven after a successful source reconciliation.

API blocking is labelled separately for jobs: existing workers continue to run when
an API is blocked. Push delivery pauses under its existing block policy. This view
does not change those behaviors. A missing/failed job worker is unavailable. No
payload, owner, key, handler source or provider endpoint is returned.

The response is bounded to 100 active components/1,000 rows. Each job store supplies
at most 250 definitions and 250 pending/running queue entries. Truncation is explicit.
It is an agenda/status view, not a cron editor or execution-history archive.

## Storage interpretation

The filesystem capacity bar includes everything sharing the data filesystem,
including non-Looprd usage. Warning starts at 80%; critical starts at 90% or less
than 1 GiB free. These are informational thresholds; nothing is deleted or blocked.

The data scan reads metadata only, does not follow symbolic links, counts hardlinks
once and stops after 20,000 entries or two seconds of traversal. Interrupted,
unreadable or skipped entries produce a partial/lower-bound result. Logical file
sizes and filesystem allocation are separate; allocation may be unavailable on
some platforms. Measurements are live approximations, not a consistent backup.

Categories include component SQLite databases/journals, temporary transfers,
telemetry/audit, bug reports, diagnostics, discovery and other runtime state.
Removed component files remain included, grouped without exposing private names.
The response never lists arbitrary file names or host paths.

PostgreSQL and Redis disk storage are in their companion volumes, outside the
runtime scan. They must not be silently omitted or shown as zero. PostgreSQL volume
size includes WAL/index/server overhead. Redis may have persistence disabled; that
does not mean it uses no memory. The storage page also displays the sampler's
container-memory figures separately. When the host lacks memory accounting these
remain explicitly unavailable; the sampler cannot infer Redis dataset usage.

Volume sizes overlap the Looprd data breakdown and must not be added together.
Container images, host logs, backups and unmounted data are not measured by the
runtime scan. Free-space monitoring still includes their impact when they share its
filesystem. Separate host filesystems need independent monitoring.

## Optional companion volume sampling

Add `--storage` to the existing host collector invocation:

```sh
python3 collect.py --output /path/to/metrics/containers.json --storage
```

Only `looprd-data`, `looprd-postgres-data`, `looprd-redis-data` and
`looprd-ntfy-data` are inspected. For configured named volumes, the host runs
read-only `du` with a three-second limit per volume. No engine socket, credentials,
mount paths or directory listings are exported. Missing/bind-mounted/slow volumes
remain unavailable; this flag does not provision a new Redis volume or enable
persistence. The snapshot remains compatible with metrics version 1; storage is
optional. Upgrade the runtime reader before enabling this new field on older nodes.

Allow an additional 12 seconds in the sampler's service timeout when enabling
volume collection. Samples older than 60 seconds are marked stale. The panel shows
the host sample's age independently of the runtime data scan age. Removing the flag
disables volume accounting without changing any service or stored data.

No new containers, application migrations, credentials or writable host mounts are
required. These views provide visibility and do not promise automatic prevention
of disk exhaustion.

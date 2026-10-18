# Backing services and container metrics

OpenAPI generation, contract validation and client SDK generation do not require
another running service. Looprd itself hosts HTTP/WebSockets and its management UI.
SQLite remains built in. PostgreSQL is an optional database companion; Redis is
optional for shared events and counters. Prometheus/Grafana and exporter containers
are optional future integrations, not dependencies of the built-in Operations view.

## Host sampler

The standard-library Python sampler in `deploy/metrics/collect.py` runs on the
host under a systemd timer. It invokes read-only Podman inspect/stats operations
for exactly `looprd`, `looprd-postgres`, `looprd-redis` and optional `looprd-ntfy`. It never lists or samples
other containers and never starts, stops or changes them. No Podman/Docker socket
is mounted in Looprd, and no new network endpoint or port is exposed.

The sampler atomically replaces a bounded sanitized JSON file containing state,
start time, restart count, CPU usage, memory accounting where available, process
count, network RX/TX bytes and block read/write bytes. CPU uses deltas between
samples; 100% means one CPU core. The initial sample and samples after restart or
a long collection gap have no CPU percentage. I/O counters cover container lifetime.
Podman's human-readable counters have limited precision. Network/disk counters
are transferred bytes, not network bandwidth or occupied filesystem space.

Memory is conservatively unavailable without the cgroups v2 memory controller.
The Pi currently lacks that controller; the UI shows unavailable rather than the
zero reported by Podman. No boot or kernel changes are made. Missing containers,
failed measurements and snapshots older than 60 seconds are explicit states.
No environment, credentials, addresses, mounts, labels, commands, raw errors or
application payloads are written. Historical container graphs are not included in
this first sampler; existing API request/latency history remains available.

## Rootless installation

Run the sampler as the same user that owns the rootless Looprd containers:

```sh
install -Dm755 deploy/metrics/collect.py ~/.local/libexec/looprd-collect-metrics.py
install -Dm644 deploy/metrics/looprd-metrics.service ~/.config/systemd/user/looprd-metrics.service
install -Dm644 deploy/metrics/looprd-metrics.timer ~/.config/systemd/user/looprd-metrics.timer
mkdir -p ~/.config/looprd/metrics
systemctl --user daemon-reload
systemctl --user enable --now looprd-metrics.timer
```

Add to the node-local `looprd.env` and restart only Looprd:

```text
LOOPRD_CONTAINER_METRICS_FILE=/etc/looprd/metrics/containers.json
```

The existing read-only configuration-directory mount makes atomic snapshots visible
inside Looprd. The directory and snapshot must be readable by its mapped user;
0644 is sufficient for the sanitized file. Keep secret files at their existing
restricted permissions. Open Operations; Auto-refresh updates visible metrics.
The protected `GET /_looprd/ops` response exposes them under `containers`.

For rootful/native systemd, install the collector at `/usr/local/libexec/` and
units in `/etc/systemd/system/`. Replace ExecStart with the absolute collector
path and the output file in the existing Looprd configuration directory. Use
`systemctl` without `--user`. Rootful and rootless Podman have separate stores;
the sampler must use the same store as the target containers. Preserve existing
container units and networks; the sampler is independent of Quadlet availability.

Disabling the timer leaves a snapshot that becomes stale. Removing the environment
setting disables metrics ingestion. Neither action changes any container or data.

## Optional PostgreSQL companion

The Storage workspace can additionally show occupied named-volume bytes, including
PostgreSQL and Redis, with the collector's optional `--storage` flag. Memory remains
a separate measurement. See [operator observation](operator-observation.md) for
sampling limits, unavailable values and overlapping size categories.

The supplied PostgreSQL Quadlets and database-network drop-in are opt-in for new
deployments. Existing installations should retain their database container and
connection secrets. Set a node-local `postgres-password` secret and `postgres.env`
from `deploy/postgres.env.example`. PostgreSQL has no published port. Mount only
the intended runtime on its private network. Provision separate component databases
or isolated schemas, then store their connection URLs as local secrets referenced
by the component's database.connectionSecret. The container is a database server,
not an automatic source of application accounts or authentication policies.

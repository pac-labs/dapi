# Run Looprd with rootless Podman Quadlet

Use a 64-bit Raspberry Pi OS/Linux installation with Podman Quadlet and cgroup v2.
The image workflow builds `linux/arm64` and `linux/amd64` variants of the same image.
The workflow publishes to `ghcr.io/pac-labs/dapi`; the package may initially be private.
Make it public or run `podman login ghcr.io` as the service user before starting.

From a checkout on the Pi, run as the account that will own the service:

```sh
mkdir -p ~/.config/containers/systemd ~/.config/looprd/secrets/roboscope
cp deploy/quadlet/looprd.container deploy/quadlet/looprd-data.volume ~/.config/containers/systemd/
cp deploy/looprd.env.example ~/.config/looprd/looprd.env
cp examples/sources.yaml examples/looprd.yaml ~/.config/looprd/
umask 077
openssl rand -hex 32 > ~/.config/looprd/secrets/roboscope/api-token
chmod 700 ~/.config/looprd/secrets ~/.config/looprd/secrets/roboscope
chmod 600 ~/.config/looprd/looprd.env
```

The Quadlet uses `ghcr.io/pac-labs/dapi:latest`. An immutable image digest
can replace `:latest` when you want controlled runtime upgrades.
Edit `~/.config/looprd/looprd.env` for browser origins and optional private GitHub credentials.
The entire configuration directory is mounted so atomically replacing a spec/sources file remains visible.

```sh
sudo loginctl enable-linger "$USER"
systemctl --user daemon-reload
systemctl --user start looprd.service
systemctl --user status looprd.service
journalctl --user -u looprd.service -f
curl http://127.0.0.1:8080/_looprd/health
```

Without an existing `secrets/admin-token`, first start prints a management API key once
to the container logs/journal. Save it and use the [web management panel](management-panel.md).
Its hash persists in the named data volume. Existing mounted tokens continue to work.

Quadlet's `[Install]` section arranges startup on subsequent user-manager boots;
do not run `systemctl enable` on the generated transient service.
`UserNS=keep-id:uid=10001,gid=10001` maps the non-root container account to the service user.
The named `looprd-data` volume retains SQLite databases and cached active specifications.
Configuration is mounted read-only; the image root filesystem is read-only as well.

Only localhost port 8080 is published. Configure your existing reverse proxy to forward
HTTP and WebSocket upgrade traffic to it and terminate TLS for your FQDN.
Public routes are `/looprd/<component>/v1/...`; `/dapi/...` is also accepted.
Use the [existing Traefik integration](traefik.md) for `api.example.com`.
Management stays private unless you explicitly install the optional authenticated
[panel router](management-panel.md).
`/_looprd/health` checks the runtime, not whether every source is currently reachable.

Optional [Redis event fanout](redis.md) has separate companion/network Quadlets and
an opt-in Looprd drop-in. Existing installations and component specs keep their
current memory delivery unless explicitly enabled. PostgreSQL remains separate.

## Remote sources

Set `LOOPRD_SOURCES=github://pac-labs/YOUR-PRIVATE-CONFIG/sources.yaml?ref=main`
to poll a GitHub sources file. Mount its token with `LOOPRD_GITHUB_TOKEN_FILE`.
See [private sources setup](private-sources.md) for read-only permissions and referenced repositories.
HTTPS URLs and local files remain supported. Specs are retrieved at resolved immutable commits.
GitHub credentials are sent only to GitHub API/raw-content hosts; redirects are rejected.

The sources document must contain `apiVersion: looprd/v1` and a `components` list.
Removing an entry from a successfully retrieved, valid document shuts that server down.
An explicit empty list shuts all logical servers down. Timeouts, missing files, HTTP errors,
and malformed documents preserve running servers. An unreachable individual spec also preserves its server.
Removing the sources file itself is therefore **not** a shutdown operation.

## Updates and backup

Component specs reload automatically; no image update is needed for them.
For a runtime image update using a mutable tag:

```sh
podman pull ghcr.io/pac-labs/dapi:latest
systemctl --user restart looprd.service
```

For a digest-pinned image, change the Quadlet image reference and run daemon-reload before restarting.
There is no automatic runtime image upgrade in this example.

For a consistent SQLite backup, stop the service and export the complete volume:

```sh
systemctl --user stop looprd.service
podman volume export looprd-data --output looprd-data-backup.tar
systemctl --user start looprd.service
```

Back up `~/.config/looprd` separately with access controls suitable for its secrets.
Use PostgreSQL-native backup tools for external databases. Restore data and the matching
cached specification state together; changing an image cannot undo schema migrations.
Removal never deletes database files or the named volume.

If the service is not generated, inspect Quadlet diagnostics:

```sh
/usr/lib/systemd/system-generators/podman-system-generator --user --dryrun
systemd-analyze --user --generators=true verify looprd.service
```

Reference: [Podman Quadlet documentation](https://docs.podman.io/en/latest/markdown/podman-systemd.unit.5.html).

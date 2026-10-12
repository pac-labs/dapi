# Management key and web panel

On first start without a mounted `secrets/admin-token`, Looprd generates a management
API key and prints it once to the container's stdout. Save that key; only its SHA-256
hash is retained in `/var/lib/looprd/admin-key.json`, with owner-only file permissions.
Restarts reuse the hash without printing a key or changing access. The data volume must persist.

```sh
podman logs looprd
# With Quadlet, the first-start output is also available in:
journalctl --user -u looprd.service
```

The first-start log contains the management credential, so access to that log is access
to server management. An existing mounted admin-token takes precedence and is never printed
or overwritten. `LOOPRD_BOOTSTRAP_ADMIN=false` disables automatic key creation; a mounted
token or previously generated hash still works. An invalid existing hash fails startup
instead of silently replacing the credential.

## Open the panel

Use `http://127.0.0.1:8080/_looprd/panel/` on the host, or forward that port over SSH:

```sh
ssh -L 8080:127.0.0.1:8080 YOUR-USER@YOUR-PI
```

For the existing HTTPS hostname, optionally copy
[`management.yml.example`](../deploy/traefik/management.yml.example) into the existing
Traefik dynamic configuration directory as `looprd-management.yml`. It uses the `looprd`
service defined by `deploy/traefik/looprd.yml` and the existing `websecure` entry point/TLS.
Then open **https://api.example.com/_looprd/panel/**.
This explicitly exposes the management API as well as the panel. The normal component
router continues to exclude management. No deployment file is applied automatically.

Paste the management key and choose **Connect**. The panel supports:

- Component status, authentication modes, available scopes, and source errors.
- Refreshing status and reconciling the configured sources immediately.
- Creating, listing, and revoking component API keys in database-auth mode.

The page keeps the management key in memory only: no cookies, browser storage, query strings,
or embedded credentials. Disconnecting or reloading clears it. Newly issued component keys
are displayed once and can be cleared. API requests use the Authorization header to the
same origin; all management actions remain protected even though the login shell is public.
The panel does not edit repository files, provision PostgreSQL servers/databases, or deploy containers.
Source changes still happen in the configured sources repository/file.

## Lost key or rotation

```sh
podman exec looprd python -m looprd admin-key --rotate
```

The command prints a new management key to that terminal, replaces the persisted hash
atomically, and invalidates the old key on the next management request. No restart is needed.
Existing panels using the old key disconnect when their next API request receives 401.
The replacement is not printed again at startup. With a mounted admin-token the command
refuses rotation; replace that host file instead. Key management never changes component keys.

`python -m looprd admin-key` can also generate the initial key before starting the server;
if one already exists it reports that without revealing or rotating it.

Design authority: `DESIGN.adoc`, “Authentication and management”.

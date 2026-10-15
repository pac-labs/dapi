# Optional Redis event fanout

Redis adds best-effort notification fanout between trusted Looprd runtimes. It
does not store authoritative application data, add replay, or make multiple ASGI
workers safe. Continue running one worker. See [the reviewed proposal](redis-review.md).

## Component contract

After upgrading the runtime, opt a component in using this additive `looprd/v1` field:

```yaml
realtime:
  backend: redis
  connectionSecret: redis-url
  delivery: best-effort
```

The secret file beneath `LOOPRD_SECRETS` contains a Redis URL, for example
`redis://looprd:YOUR-LOCAL-PASSWORD@looprd-redis:6379/0`. Use `rediss://` for a remote
TLS-protected server. Credentials belong only in the mounted local file, never in
the descriptor. Query parameters and fragments are rejected. An absent/invalid
secret rejects the new generation and preserves the working one.

Omitting `realtime` preserves the in-memory bus. Explicit `backend: memory` is also
accepted, without a connectionSecret. This guide describes best-effort delivery;
SQL-backed `delivery: durable` is documented in [durable events](durable-events.md).
`ctx.publish(event)` remains synchronous; WebSocket subscribe messages and event
payloads remain unchanged. Existing resource permissions, key revocation checks,
origin checks, tickets, and API blocks still apply. Redis clients are infrastructure
peers, not unauthenticated end users.

Local events are delivered immediately. Remote sends have a bounded queue and
message size (1 MiB). A full queue, unavailable Redis, malformed envelope, or failed
send may drop a remote event; HTTP writes remain successful. Redis reconnects with
backoff. There is no replay of an outage and no transaction coupling SQL to Redis.
Clients must re-fetch authoritative state after reconnecting or missing an update.

Protected status includes per-component realtime state, pending/dropped remote
event counts and an exception class, without URLs, credentials, channels or event
contents. Component details show event transport state. Liveness remains separate.

Channels are `<LOOPRD_EVENT_NAMESPACE>:<component>:<api-version>:events`. Set a unique
host namespace for unrelated environments, including tests. Redis database numbers
do not isolate Pub/Sub. Peers intentionally sharing a namespace/component/version
must serve the same data and subscription authorization contract. Self-origin and
recent duplicate envelopes are suppressed, including overlapping reload connections.
This bounded suppression is not an exactly-once guarantee.

## Companion container

The optional rootless Quadlets use a private internal network, no host port,
128 MiB Redis maxmemory and a 192 MiB container memory limit. Redis persistence is
disabled because Pub/Sub is ephemeral. PostgreSQL/SQLite remain authoritative.

For a fresh setup, create a local Redis ACL and connection file without printing
the password. The default namespace below matches `LOOPRD_EVENT_NAMESPACE=looprd`:

```sh
python3 - <<'PY'
import hashlib, os, secrets
from pathlib import Path
root = Path.home() / '.config/looprd/secrets'
root.mkdir(parents=True, exist_ok=True, mode=0o700)
paths = [root / 'redis.acl', root / 'redis-url']
if any(path.exists() for path in paths):
    raise SystemExit('Redis configuration exists; rotate it deliberately instead of overwriting')
password = secrets.token_urlsafe(32)
digest = hashlib.sha256(password.encode()).hexdigest()
acl = ('user default off\nuser looprd on #' + digest +
       ' &looprd:* +ping +publish +subscribe +unsubscribe +echo +client|setinfo +client|setname\n')
for path, value in zip(paths, [acl, 'redis://looprd:' + password + '@looprd-redis:6379/0\n']):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as file:
        file.write(value)
PY
mkdir -p ~/.config/containers/systemd/looprd.container.d
cp deploy/quadlet/looprd-events.network deploy/quadlet/looprd-redis.container ~/.config/containers/systemd/
cp deploy/quadlet/looprd.container.d/redis.conf.example ~/.config/containers/systemd/looprd.container.d/redis.conf
systemctl --user daemon-reload
systemctl --user start looprd-redis.service
systemctl --user restart looprd.service
```

Set the matching channel prefix in the ACL if using a custom namespace. Verify
the ACL file remains readable by the mapped container user and owner-only on the
host. The Looprd drop-in explicitly adds `Network=podman` for outbound GitHub/SQL
connectivity alongside the internal events network. Preserve existing shared
Traefik/SQL networks and adapt this outbound network to your installation. A host
network configuration cannot be combined blindly with these bridge networks.

The companion has `Wants`/startup ordering, rather than a hard dependency that stops
Looprd during Redis failure. Existing components remain on memory until their
specifications opt in. Mount the configuration directory read-only as before.
Rotate credentials through a controlled ACL update and component reload/restart;
editing a secret alone does not reload an unchanged active specification.

On older Podman without Quadlet, use the same image/command, ACL mount, private
network and limits in a native systemd unit (or that Podman version's generated
systemd unit). Preserve Looprd's existing networks and published addresses. Do not
replace unrelated node units or expose port 6379 just to connect the companion.

## Upgrade and rollback

Existing descriptors need no changes. New runtimes read old descriptors and cached
state. Memory-only checkpoints omit the new field, preserving their old format.
Old strict validators cannot accept new fields: upgrade the runtime before adding
realtime to component repositories. Before downgrading, remove realtime from sources,
reconcile and verify a memory checkpoint, then stop/update the runtime. Offline
rollback with cached Redis descriptors requires restoring a compatible checkpoint.
The transport introduces no application database migration and deletes no SQL data.

## Validation

Focused tests cover fanout, echo/deduplication, namespace/component isolation, queue
overflow, outages/reconnect, invalid envelopes, failed candidates/checkpoints,
removal and connection cleanup. CI starts real Redis and checks cross-runtime
delivery plus command/channel ACL permissions. Local real-server tests require
`LOOPRD_TEST_REDIS_URL` pointing only at a disposable Redis server; they create and
remove a randomly named ACL user. Never point them at a production Redis instance.

References: [Redis Pub/Sub semantics and scoping](https://redis.io/docs/latest/develop/pubsub/),
[redis-py async connections](https://redis.readthedocs.io/en/stable/examples/asyncio_examples.html).

## Optional shared rate counters

Pub/Sub permissions above remain sufficient for events. Components opting into
`limits.backend: redis` additionally need only their namespace's limit keys and
counter transaction commands. Append to the mounted ACL user locally:

```text
~looprd:limits:* +multi +exec +discard +incr +expire
```

Adjust the prefix with LOOPRD_EVENT_NAMESPACE. No GET, enumeration or application
keys are needed. Restart only the Redis companion after an ACL-file change, then
verify both fanout and configured limits. Existing components keep their narrower
ACL and memory behavior until explicitly opted in.

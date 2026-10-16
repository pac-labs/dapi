# Looprd validation

`tests/test_administration.py` covers DESIGN.adoc / Specification authority /
Operator data administration: opt-in/projections, private grants, choices and
bounds, immutable ownership/revision fields including aliases, atomic edits/actions,
rollback on audit failure, concurrent stale writes, cancellation protection,
HMAC ETags, idempotent receipts, event notifications, blocked-API repairs and
restart/opt-out retention. `tests/test_operator_postgresql.py` exercises real
PostgreSQL audit/outbox transactions in an isolated schema when the existing
disposable test URL is configured. UI tests cover typed controls, literal text,
confirmation-key reuse and disabled controls. Browser checks use only synthetic
accounts, including role/balance actions, audit, drafts, search/paging and phone layout.

`tests/test_bugreports.py` covers DESIGN.adoc / Purpose / Central application bug
reports: authenticated submission, public mode, CORS and blocking, protected review,
deduplication and concurrent counts, the global 100-issue cap and eviction policy,
restart/removal retention, bounded JSON, rate limits, secret redaction, safe storage
failures, reserved routes and generated OpenAPI discovery. UI checks cover text-only
rendering, filtering and confirmed deletion; browser verification uses synthetic
reports and a test-only management key.

`tests/test_cors.py` covers DESIGN.adoc / Specification authority / Component
frontend origins: component isolation, both URL aliases, HTTP and WebSocket parity,
authentication, management isolation, legacy fallback, private additions and
restrictions, hot reload and failed-candidate retention, and strict origin validation.

Governing design: `DESIGN.adoc`, sections “Sources and shutdown semantics”,
“Generation reload”, “Database lifecycle”, and “Authentication and management”. This repository is the standalone Looprd
companion; it does not modify the parent ZipRunner application.

```sh
npm ci --ignore-scripts
npm run build:ui
npm run test:ui
python -m pip install -r requirements.lock -e '.[test]'
python -m pytest -q tests/test_specs.py tests/test_runtime.py
python -m pytest -q tests/test_database_auth.py tests/test_websocket.py tests/test_http.py
python -m pytest -q tests/test_admin.py
python -m pytest -q tests/test_dashboard.py
python -m pytest -q tests/test_availability.py
python -m pytest -q
python -m looprd validate examples/looprd.yaml
python -m looprd validate --sources examples/sources.yaml
python -m looprd validate examples/python-handler.yaml
python -m looprd validate examples/database-auth.yaml
```

The suite covers authoritative removal versus unreachable/malformed sources, cached offline recovery,
database retention after removal/re-addition/restart, invalid reload preservation, independent components,
HTTP input validation and authentication, OpenAPI, additive migration rollback, WebSocket tickets/events,
and generation reconnect behavior. The PostgreSQL integration check runs when
`LOOPRD_TEST_POSTGRES_URL` points at a dedicated disposable database; CI provisions it.
Do not point that test at a production database.

`tests/test_private_sources.py` covers token-file precedence/rotation, private index and referenced
repository reads via the GitHub Contents API, same-commit relative specifications, and preventing
credentials from following redirects or reaching other hosts.

`tests/test_database_auth.py` covers permission completeness, all-of scope checks, custom-handler
identity, component isolation, hashed storage, protected provisioning, retained keys and revocations,
expiry, denied subscriptions, outstanding-ticket invalidation, and idle WebSocket disconnects.
The PostgreSQL CI integration also creates scoped keys, retains them across an application migration,
and verifies revocation. Local runs skip that check unless a disposable PostgreSQL URL is configured.

`tests/test_admin.py` verifies first-start key output, hash-only persistence, restart retention,
mounted-token precedence, opt-out, invalid-state handling, CLI rotation, old-key rejection, and
the public panel shell versus protected management routes. Browser checks use a disposable local
preview instance with a test-only mounted token; never use production credentials for UI tests.

`tests/test_dashboard.py` covers safe route/model diagnostics, live socket counts,
source failure timestamps, disabled/unloaded components, and omission of handler code and credentials.

Build and test a container on a machine with a running Docker/Podman engine:

```sh
podman build -f Containerfile -t looprd:smoke .
python scripts/container_smoke.py podman looprd:smoke --traefik
```

The smoke script creates only its own temporary configuration, container, and named volume,
then removes those resources. With `--traefik` it also checks the shipped Traefik router
using an isolated test proxy/network: correct host and prefixes route, other paths and management do not.
The smoke reads its Host header from the deployed router rule, with no duplicated hostname.
`tests/test_traefik_smoke.py` checks custom-host adaptation and rejects unsupported or ambiguous rules.
It also verifies published router examples use the placeholder hostname; private deployment
customizations belong outside the source checkout.
This exercises HTTP routing; the production TLS certificate remains managed by the existing Traefik.
CI runs it using Docker before publishing multi-platform images.
The smoke rereads the published port after restart because ephemeral host ports may change.
`tests/test_container_smoke.py` verifies the complete smoke sequence follows the new port.
ARM64 image building is automated; a Raspberry Pi/Quadlet operator run remains a deployment check.
Check `docs/quadlet.md` for the installation, persistence, and backup procedure.

Generated schemas can be refreshed with `python -m looprd schema` and
`python -m looprd schema --sources`; save the UTF-8 JSON output into `schemas/`.

`tests/test_availability.py` covers protected strict blocking, both URL aliases,
retained records, reload/restart/removal behavior, other-component isolation, live
and draining WebSocket closure, ticket invalidation, corrupt state, write failures
and cancellation while persisting. Browser checks cover section navigation, contained scrolling, mobile layout, confirmation, and retained key workflows using local test data.
`npm run test:ui` checks saved-key reconnection, opt-out, forgetting credentials,
and denied browser storage. Browser checks additionally verify reload and Forget behavior.


`tests/test_files.py` covers the DESIGN.adoc Temporary file transfers contract:
private invitation/list/delete boundaries, raw single-use uploads, anonymous unlisted
attachment downloads/ranges, restart persistence, immediate expiry and physical cleanup,
filename/body limits, quota reservations, cancellation and crash recovery, and invitation caps.
Node tests cover shell quoting. Container/Traefik smoke exercises a disposable file round trip
through the shipped public upload/download rules and verifies management remains excluded.
Browser checks use `.local/files-preview` with synthetic bytes and a test-only mounted token.


Redis fanout: `python -m pytest -q tests/test_redis_events.py tests/test_redis_integration.py`
checks DESIGN.adoc Optional Redis event fanout. The default suite uses a deterministic
transport substitute for outages, reconnects, queue bounds and candidate ownership.
The real integration check requires a disposable `LOOPRD_TEST_REDIS_URL`; CI provisions
Redis 7.4 for both Python jobs and tests actual fanout/echo plus restricted channel/command ACLs.
Never use a production Redis URL: the real test creates/removes a uniquely named ACL user.
Legacy checkpoints, descriptors, synchronous handlers and WebSocket wire messages remain covered.

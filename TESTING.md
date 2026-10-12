# Looprd validation

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

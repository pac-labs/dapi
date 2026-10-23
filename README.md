# Looprd

Independent nested components are available in the [0.4.0 subcomponents contract](docs/subcomponents.adoc).
Each child owns its authentication, database, jobs and lifecycle. Existing `includes`
continues to compose fragments of one component.

## Optional service registrations (0.3.0)

Components may declare `registrations: [{service: atrium, document: atriumd.yaml}]`.
Authorized consumers pull their own metadata without becoming runtime dependencies.
See [generic service registrations](docs/service-registrations.adoc). Existing
Atrium push discovery remains compatible; consumer pull clients are separate work.

One Python companion server hosts small APIs defined in self-contained project specifications.
A specification includes data models, CRUD routes, WebSocket events, authentication policy,
inline custom handlers, and database migrations. Components hot reload inside the shared runtime.
SQLite needs no extra container; PostgreSQL is also supported through SQLAlchemy.

Source: [pac-labs/dapi](https://github.com/pac-labs/dapi). Image: `ghcr.io/pac-labs/dapi`.
The existing Traefik instance serves Looprd paths on your configured API hostname.

Version 0.2.0 adds optional [automatic Atrium discovery delivery](docs/automatic-atrium-discovery.adoc)
with durable retries and typed Git/local/HTTPS provenance. Defaults remain off;
it requires a matching Atrium v2 machine receiver. Source ZIP import never deploys
or enables this integration automatically.

The Carbon operator workspace supports service controls, issue review and optional
[data administration](docs/operator-administration.md). Components explicitly
declare readable/editable fields and trusted management actions in `looprd.yaml`;
confirmed changes use transactions, stale-record protection and audit receipts.

All components expose centralized [application bug reporting](docs/bug-reports.md).
New and updated clients must support consented diagnostic submission. The protected
management panel groups repeated issues and retains at most 100 distinct issues.

**Removal is explicit:** removing a component from a successfully fetched, valid sources file
shuts its server down and keeps its data. Unreachable or malformed sources/specifications preserve
working servers. Cached specifications allow restart while sources are offline.

## Run locally

Python 3.12 or newer:

```sh
python -m venv .venv
. .venv/bin/activate
npm ci --ignore-scripts
npm run build:ui
python -m pip install -r requirements.lock -e '.[test]'
mkdir -p secrets/roboscope
umask 077
openssl rand -hex 32 > secrets/roboscope/api-token
python -m looprd
```

On Windows, activate with `.venv\Scripts\Activate.ps1`; create the same secret files
with your preferred token generator. The default sources file is `examples/sources.yaml`.
Local data and the restart checkpoint live under `state/`.
On first start, Looprd prints a management API key once unless an admin-token file already
exists. Save it and paste it into `http://localhost:8080/_looprd/panel/`.

```sh
curl http://localhost:8080/_looprd/health
curl -H "Authorization: Bearer $(cat secrets/roboscope/api-token)" \
  http://localhost:8080/looprd/roboscope/v1/scans
curl -H "Authorization: Bearer $LOOPRD_ADMIN_KEY" \
  http://localhost:8080/_looprd/status
```

For the management curl example, set `LOOPRD_ADMIN_KEY` to the key from your terminal.

## Add a server

Put [looprd.yaml](examples/looprd.yaml) in a project and add it to your sources file:

```yaml
apiVersion: looprd/v1
components:
  - id: roboscope
    repository: https://github.com/YOUR-OWNER/YOUR-PROJECT.git
    ref: main
    specification: looprd.yaml
```

Use a local `specification` path or HTTPS URL instead of `repository` for other sources.
Polls default to 60 seconds. A component's ID determines its URL and persistent database identity.
No component container build is necessary.

- [Specification contract, custom handlers, and migrations](docs/specification.md)
- [Database-backed API keys, operation scopes, and revocation](docs/authentication.md)
- [First-start management key and web panel](docs/management-panel.md)
- [Operational dashboard, routes, models and reload diagnostics](docs/dashboard.md)
- [Sources example](examples/sources.yaml)
- [Generated JSON schemas](schemas)
- [Podman Quadlet installation on a Raspberry Pi](docs/quadlet.md)
- [Add routing to the existing Traefik instance](docs/traefik.md)
- [Read a private sources repository with a mounted GitHub token](docs/private-sources.md)
- [GitHub Actions image workflow](.github/workflows/images.yml)
- [Architecture](DESIGN.adoc) and [testing](TESTING.md)

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `LOOPRD_SOURCES` | `examples/sources.yaml` | Local, HTTPS, or `github://OWNER/REPO/path?ref=main` document |
| `LOOPRD_DATA` | `state` | Persistent databases and active spec checkpoint |
| `LOOPRD_SECRETS` | `secrets` | Directory of secret-reference files |
| `LOOPRD_PORT` | `8080` | Shared listening port |
| `LOOPRD_POLL_SECONDS` | `60` | Reconciliation interval |
| `LOOPRD_DRAIN_SECONDS` | `30` | WebSocket reload/removal drain |
| `LOOPRD_REQUEST_SECONDS` | `30` | HTTP request deadline |
| `LOOPRD_ORIGINS` | empty | Comma-separated allowed browser origins |
| `LOOPRD_ORIGIN_RESTRICTIONS` | unset | Optional ceiling on component frontend origins; empty denies all browser origins |
| `LOOPRD_GITHUB_TOKEN` | unset | Read-only access to private GitHub sources |
| `LOOPRD_GITHUB_TOKEN_FILE` | unset | Mounted GitHub token file; takes precedence over the environment token |
| `LOOPRD_ALLOW_PYTHON` | `false` | Enable trusted inline Python handlers |
| `LOOPRD_BOOTSTRAP_ADMIN` | `true` | Generate and print a management key once if none is configured |

Management routes require the generated management key or mounted `admin-token` secret.
The optional panel Traefik router enables HTTPS access; otherwise management stays on the private backend.
Run one ASGI worker. TLS is provided by your reverse proxy. Custom Python is trusted code sharing
runtime permissions; leave it disabled for declarative-only hosting.

The workflow tests and builds `linux/amd64` and `linux/arm64` images, publishing to
`ghcr.io/pac-labs/dapi` on main/master pushes, version tags, and manual runs in that repository.
Looprd's status/reconciliation API is ready for a ZipRunner companion integration;
this repository does not modify ZipRunner's UI or project configuration format.

## Optional generic platform features

See the [platform reference](docs/platform-reference.md) for verified principals,
private record policies, data/query/service contracts, limits, jobs and operations;
and [durable events](docs/durable-events.md) for replay. No external identity
provider is required. Application accounts and domain behavior stay in components.

See [container metrics and backing services](docs/container-metrics.md) for the
optional host sampler, Operations resource cards and PostgreSQL companion templates.

See [agent diagnostics](docs/diagnostics.md) for expiring read-only diagnostic URLs,
machine-readable health/failure reports and safe migration error explanations.

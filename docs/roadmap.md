# Platform status and future work

The four generic passes add durable events, optional verified principals, record
policies, data/query/service contracts and operational controls. See
[platform reference](platform-reference.md) and [accepted scope](platform-passes.md).

Applications own registration, recovery and device sessions. External identity
infrastructure is not required. OIDC may become an optional verification adapter.

Future work: full active-active coordination of tickets, uploads, blocks and reloads;
security isolation for untrusted code; named operators and immutable audit export;
configurable telemetry export and broader database-health probes. Redis, SQL job
leases and process execution do not imply those guarantees.

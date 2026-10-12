# Suggested next steps

These are proposals, not enabled features or changes to component authentication.

1. **Audit history:** record operator actions, source changes, failed reloads and key
   issuance/revocation. Exclude raw credentials, request bodies and handler code.
   Named operators would make attribution more useful than a shared management key.
2. **Traffic and reliability:** bounded per-component request/error counters,
   latency summaries, recent failures and database connectivity checks. Current
   dashboard counters show in-flight work, not historical usage or database health.
3. **Rate limits:** component and principal quotas, request bursts and WebSocket
   connection limits. Define how limits behave across restarts before adding them.
4. **Shared identity:** one identity layer for user login across components, with
   explicit component-specific grants and separate operator permissions. Keep
   scoped machine API keys and application ownership checks; a shared login must
   not automatically grant access to every API or every row.

For shared identity, decide between an operator-selected OpenID Connect provider
and a Looprd-owned account/session service backed by a dedicated PostgreSQL
database. OIDC offers a standard integration boundary for existing providers;
a built-in service fits the single-runtime model but requires account recovery,
credential storage, session revocation, abuse controls and a complete maintenance
contract. It should be an explicitly designed follow-up, not an implicit change
to existing public/bearer/database specifications.

A proposed component contract would select the identity provider and declare
its audience and required scopes. Looprd would validate the identity and pass
a stable principal to handlers; handlers would still enforce record ownership.
Operator access would remain separate, with the current management key kept as
an emergency access path if explicitly chosen. Browser sessions and machine keys
need separate lifetimes and revocation rules.

References: [OpenID Connect Core](https://openid.net/specs/openid-connect-core-1_0.html)
and [Keycloak application integration](https://www.keycloak.org/securing-apps/overview).

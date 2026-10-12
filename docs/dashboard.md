# Management dashboard

The panel uses IBM Carbon v11 styles with the Gray 100 theme. Its styles are bundled
in the container; no CDN or external font requests are required. For source builds,
see [panel development](../ui/README.md).

Open `/_looprd/panel/` on your Looprd host and connect with the management key.
The dashboard refreshes every ten seconds while connected and visible. Turn off
auto-refresh to pause it; **Refresh** requests a new snapshot without checking
repositories. **Check sources now** immediately fetches the sources and component
specifications, validates updates, and applies working generations.

The overview shows active components, errors, open WebSockets and the last source
check. Search or filter the component list, then select a component to inspect:

- Its active release, Git revision, tracked repository/ref and process load time.
- Authentication mode, database backend/schema version, and live request/socket counts.
- Full component URL, declared HTTP and WebSocket routes, and required HTTP scopes.
- Database models, field types, lengths and constraints. No database rows are fetched.

An active component with an update error keeps serving its last working version.
An enabled component that cannot load is shown with its error even when it has no
active generation. Disabled entries are displayed separately. Removing an entry
from a valid sources document removes it from the dashboard; its stored data is kept.
Unreachable/invalid sources retain the last valid configuration and show a warning.

Runtime details show process uptime, reconciliation timestamps, draining generations,
Python-handler policy and allowed browser origins. A completed reconciliation may
still include individual component failures; check the error count. These values reset
on restart and do not represent host/container CPU, memory, historical traffic, or
database connectivity probes. Request counts cover work still in flight at the time
of the snapshot, not requests per second.

The API-key section appears for components using Looprd `database` authentication.
Create scoped keys, copy the newly issued value once, inspect expiry/revocation and
confirm revocation. Public/shared-bearer components may implement their own access
checks; a public Life-style session API does not use this key store.

Credentials stay in page memory. Disconnect/reload clears them; no cookies or browser
storage are used. Diagnostics omit handler code, response bodies, source URLs and
credential references. The raw snapshot remains available in a collapsed details view.
The dashboard does not edit the sources file, inspect application rows, update images,
or provision database accounts. Dashboard updates require a Looprd image update.

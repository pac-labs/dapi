# Management dashboard

The panel uses IBM Carbon v11 styles with the Gray 100 theme. Its styles are bundled
in the container; no CDN or external font requests are required. For source builds,
see [panel development](../ui/README.md).

Open `/_looprd/panel/` on your Looprd host and connect with the management key.
The dashboard refreshes every ten seconds while connected and visible. Turn off
auto-refresh to pause it; **Refresh** requests a new snapshot without checking
repositories. **Check sources** immediately fetches the sources and component
specifications, validates updates, and applies working generations.

The fixed workspace uses Overview, Components, Access keys, Runtime and Diagnostics
buttons instead of a scrolling document. Component details have Summary, API routes
and Database models tabs. Long lists, schemas and diagnostics scroll inside their
panels; the overall page stays fixed.

The overview shows serving components, errors, open WebSockets and the last source
check. Search or filter the component list, then select a component to inspect:

- Its active release, Git revision, tracked repository/ref and process load time.
- Authentication mode, database backend/schema version, and live request/socket counts.
- Full component URL, declared HTTP and WebSocket routes, and required HTTP scopes.
- Database models, field types, lengths and constraints. No database rows are fetched.

An active component with an update error keeps serving its last working version.
An enabled component that cannot load is shown with its error even when it has no
active generation. Disabled entries are displayed separately. Removing an entry
from a valid sources document removes it from the dashboard unless an operator block
is still recorded; its stored data is kept.
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

**Remember on this browser** is enabled by default. After a successful connection,
the management key is saved in this origin’s browser localStorage and reconnects on
reload. **Disconnect** stops the live session and keeps the saved key. **Forget key**
removes the saved key and disconnects. Uncheck Remember for session-only access;
rejected saved keys are removed. Browser storage failures fall back to session access.
This saves a bearer credential on the browser profile; use session-only access on a
shared device. No cookies are used. Diagnostics omit handler code, response bodies, source URLs and
credential references. The raw snapshot is available in the Diagnostics panel.
The dashboard does not edit the sources file, inspect application rows, update images,
or provision database accounts. Dashboard updates require a Looprd image update.

## Block or unblock an API

Select a component, click **Block API**, then confirm. All component HTTP routes
(including `/dapi` aliases, docs and ticket issuance) return 503. New WebSockets
are denied and existing sockets, including draining generations, close with 1008.
Already executing HTTP requests may finish. Other components and management stay
available. Data, keys and the loaded specification are retained.

The block is a node-local override in the persistent data volume. It survives
source updates, restart, and removal/re-addition. Explicit **Unblock API** clears
it; unblocking does not re-enable a removed or source-disabled component.
A blocked component can still receive specification updates. This is a traffic
control, not a sandbox for trusted Python or a way to stop its initialization.

The management API is `PUT /_looprd/components/<id>/availability`, using the
management bearer key and JSON `{"blocked": true}` or `{"blocked": false}`.
Status includes a `blocked` ID list and a boolean on each loaded component.
Unknown IDs return 404; malformed bodies return 422. Invalid saved overrides
fail startup, and failed writes preserve the previous override.

See [suggested next steps](roadmap.md) for audit history, traffic limits and shared identity.

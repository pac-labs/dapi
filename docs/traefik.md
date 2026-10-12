# Add Looprd to the existing Traefik instance

Examples use **api.example.com**; substitute your configured API hostname.
Traefik already receives port 443 and continues to
own TLS and the FQDN. Add one Looprd router/service to that instance's file provider.
The router forwards `/looprd/...` and `/dapi/...` unchanged; every present and future
component is covered without adding a Traefik router per component.

## Dynamic configuration

Copy [deploy/traefik/looprd.yml](../deploy/traefik/looprd.yml) into the **existing** Traefik
dynamic configuration directory. Keep the entry point name aligned with the one serving
443 and replace `api.example.com` in its `Host` rule with your hostname on the deployment host.
Keep that customized copy outside the public source repository. The example uses
`websecure`. Its priority of 1000 must exceed your existing
whole-domain/catch-all router. Other paths keep their existing routes.

Do not add StripPrefix: Looprd expects `/looprd/<component>/<version>` or `/dapi/...`.
The same HTTP router forwards WebSocket upgrades. No separate WebSocket port is needed.
The router excludes `/_looprd/...`; the service health probe can still use that path internally.
If your existing whole-domain router forwards all paths to Looprd, remove that forwarding
or add your existing private-management policy before exposing it publicly.

`tls: {}` uses the certificate already available in Traefik. If this host's certificate is
issued through a named certificate resolver and is not already available, set
`tls.certResolver` to that **existing** resolver name. This config creates no new resolver.
The domain needs a matching existing certificate; Looprd does not issue one.

If a file provider is already enabled, use it. Otherwise merge
[file-provider.fragment.yml](../deploy/traefik/file-provider.fragment.yml) into the existing
**static** Traefik config and restart that instance once. For containerized Traefik, mount
the entire dynamic directory, rather than only a single file, so replacements are detected.
Subsequent dynamic file changes are watched.

## Select a reachable backend

The supplied file uses `http://127.0.0.1:8080`, matching Looprd's default localhost-only Quadlet port.
This works when Traefik runs directly on the same host or uses that host's network namespace.
Inside an ordinary Traefik container, `127.0.0.1` refers to Traefik itself.

If both containers use the **same Podman engine/user and existing network**, copy the optional drop-in:

```sh
mkdir -p ~/.config/containers/systemd/looprd.container.d
cp deploy/quadlet/looprd.container.d/shared-traefik-network.conf.example \
  ~/.config/containers/systemd/looprd.container.d/traefik.conf
```

Edit `Network=traefik` to the existing Podman network name and set the Traefik backend URL
to `http://looprd:8080`. Then reload/restart Looprd:

```sh
systemctl --user daemon-reload
systemctl --user restart looprd.service
```

The network alias is `looprd`. The drop-in does not create or replace Traefik's network.
Rootful and rootless engines, different rootless users, and Docker versus Podman do not share
container networks merely because their names match.

For separate machines or separate engines, use a host address reachable by Traefik.
Bind Looprd only to the selected private host interface with a Quadlet drop-in, for example:

```ini
[Container]
PublishPort=
PublishPort=192.168.1.50:8080:8080
```

Replace the illustrative LAN IP with the Pi's real private IP, use that same IP in the Traefik
service URL, and restrict port 8080 to the Traefik host through your existing firewall.
Keep management accessible only over your private path. Do not expose port 8080 through the router.

## Verify routing

To serve the management panel on this hostname as well, see the
[optional management router](management-panel.md). It exposes `/_looprd/` explicitly;
management API requests require the key printed at first container startup or a mounted admin token.

```sh
curl -H "Authorization: Bearer $(cat ~/.config/looprd/secrets/roboscope/api-token)" \
  https://api.example.com/looprd/roboscope/v1/scans
```

Verify that Traefik's dashboard shows `looprd@file`, its backend health check is green,
and existing non-Looprd routes still work. Browser clients must have their **UI origin**
listed in `LOOPRD_ORIGINS`; this may differ from the API hostname.
Protect or disable Traefik access logging of WebSocket ticket query strings using your
existing logging policy; Looprd itself disables HTTP access logs.

References: [Traefik file configuration](https://doc.traefik.io/traefik/reference/routing-configuration/other-providers/file/)
and [rules and priority](https://doc.traefik.io/traefik/reference/routing-configuration/http/routing/rules-and-priority/).

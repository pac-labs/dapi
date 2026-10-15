"""Declared service contracts preserve caller identity and scoped authorization."""

import yaml
from starlette.testclient import TestClient

from looprd.app import create_app
from test_http import ADMIN


def test_service_discovery_call_binding_and_incompatible_reload(setup_project):
    settings, _, write_sources = setup_project
    settings.allow_python = True
    shape = {"type": "object", "properties": {"value": {"type": "string", "maxLength": 16}}, "required": ["value"]}
    server = {"apiVersion": "looprd/v1", "component": "target", "release": "1", "api": {"version": "v1"},
        "auth": {"mode": "database", "scopes": ["invoke"]}, "database": {"backend": "sqlite"},
        "handlers": [{"path": "/echo", "method": "POST", "kind": "python", "source": "async def handle(ctx): return ctx.body", "scopes": ["invoke"],
            "contract": {"name": "echo", "version": 1, "allowedCallers": ["caller"], "request": shape, "response": shape}}]}
    caller = {"apiVersion": "looprd/v1", "component": "caller", "release": "1", "api": {"version": "v1"},
        "auth": {"mode": "bearer", "secretRef": "caller-token"},
        "dependencies": [{"component": "target", "contract": "echo", "version": 1, "credentialSecret": "service-key"}],
        "handlers": [{"path": "/invoke", "method": "POST", "kind": "python",
            "source": "async def handle(ctx): return await ctx.call('target', 'echo', ctx.body)"}]}
    root = settings.data.parent
    def save(name, value):
        (root / (name + ".yaml")).write_text(yaml.safe_dump(value), encoding="utf-8")
    save("target", server)
    write_sources([{"id": "target", "specification": "target.yaml"}])
    (settings.secrets / "caller-token").write_text("caller-proof")
    with TestClient(create_app(settings)) as client:
        ordinary = client.post("/_looprd/components/target/keys", headers=ADMIN,
            json={"label": "ordinary", "scopes": ["invoke"]}).json()
        assert client.post("/looprd/target/v1/echo", json={"value": "x"},
            headers={"Authorization": "Bearer " + ordinary["apiKey"]}).status_code == 403
        key = client.post("/_looprd/components/target/keys", headers=ADMIN,
            json={"label": "caller machine", "service": "caller", "scopes": ["invoke"]}).json()
        (settings.secrets / "service-key").write_text(key["apiKey"])
        save("caller", caller)
        write_sources([{"id": name, "specification": name + ".yaml"} for name in ("target", "caller")])
        assert not client.post("/_looprd/reconcile", headers=ADMIN).json()["errors"]
        headers = {"Authorization": "Bearer caller-proof"}
        discovery = client.get("/looprd/caller/v1/_services", headers=headers).json()
        assert discovery["services"][0]["available"]
        assert key["apiKey"] not in str(discovery) and "credentialSecret" not in str(discovery)
        response = client.post("/looprd/caller/v1/invoke", json={"value": "hello"}, headers=headers)
        assert response.status_code == 200 and response.json() == {"value": "hello"}
        assert client.post("/looprd/caller/v1/invoke", json={"unexpected": True}, headers=headers).status_code == 422
        previous = client.app.state.runtime.active["target"]
        server["handlers"][0]["contract"]["version"] = 2
        save("target", server)
        client.post("/_looprd/reconcile", headers=ADMIN)
        assert client.app.state.runtime.active["target"] is previous
        assert client.post("/looprd/caller/v1/invoke", json={"value": "retained"}, headers=headers).status_code == 200
        client.put("/_looprd/components/target/availability", json={"blocked": True}, headers=ADMIN)
        assert client.post("/looprd/caller/v1/invoke", json={"value": "blocked"}, headers=headers).status_code == 503
        client.put("/_looprd/components/target/availability", json={"blocked": False}, headers=ADMIN)
        client.delete("/_looprd/components/target/keys/" + key["id"], headers=ADMIN)
        assert client.post("/looprd/caller/v1/invoke", json={"value": "revoked"}, headers=headers).status_code == 403

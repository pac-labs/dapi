"""Optional consumers do not disable producers; machine authority stays enforced."""

import yaml
from starlette.testclient import TestClient

from looprd.app import create_app
from test_http import BASE, HEADERS, ADMIN


def test_optional_missing_consumer_allows_activation_upgrade_and_restart(setup_project, spec_data):
    settings, write, _ = setup_project
    settings.allow_python = True
    spec_data["dependencies"] = [{"component": "social", "contract": "notify", "version": 1,
                                  "credentialSecret": "not-provisioned-yet", "optional": True}]
    spec_data["handlers"] = [{"path": "/call", "kind": "python",
        "source": "async def handle(ctx): return await ctx.call('social','notify',{})"}]
    write(spec_data)
    with TestClient(create_app(settings)) as client:
        assert client.get(BASE + "/items", headers=HEADERS).status_code == 200
        discovered = client.get(BASE + "/_services", headers=HEADERS).json()["services"][0]
        assert discovered["optional"] and not discovered["available"]
        assert client.get(BASE + "/call", headers=HEADERS).status_code == 503
        spec_data["release"] = "2"
        write(spec_data)
        client.post("/_looprd/reconcile", headers=ADMIN)
        assert client.app.state.runtime.active["demo"].spec.release == "2"
        assert not client.app.state.runtime.errors
    with TestClient(create_app(settings)) as client:
        assert client.get(BASE + "/items", headers=HEADERS).status_code == 200


def test_optional_target_arrival_contract_change_and_credentials(setup_project, spec_data):
    settings, write, write_sources = setup_project
    settings.allow_python = True
    target = {"apiVersion": "looprd/v1", "component": "social", "release": "1", "api": {"version": "v1"},
        "auth": {"mode": "database", "scopes": ["notify"]}, "database": {"backend": "sqlite"},
        "handlers": [{"path": "/notify", "method": "POST", "kind": "response", "response": {"ok": True}, "scopes": ["notify"],
            "contract": {"name": "notify", "version": 1, "allowedCallers": ["demo"]}}]}
    spec_data["dependencies"] = [{"component": "social", "contract": "notify", "version": 1,
                                  "credentialSecret": "service", "optional": True}]
    spec_data["handlers"] = [{"path": "/call", "kind": "python", "source": "async def handle(ctx): return await ctx.call('social','notify',{})"}]
    write(spec_data)
    path = settings.data.parent / "social.yaml"
    path.write_text(yaml.safe_dump(target))
    write_sources([{"id": "demo", "specification": "looprd.yaml"}, {"id": "social", "specification": "social.yaml"}])
    with TestClient(create_app(settings)) as client:
        assert client.get(BASE + "/call", headers=HEADERS).status_code == 503  # Missing credential, not producer failure.
        wrong = client.post("/_looprd/components/social/keys", headers=ADMIN, json={"label": "ordinary", "scopes": ["notify"]}).json()
        (settings.secrets / "service").write_text(wrong["apiKey"])
        assert client.get(BASE + "/call", headers=HEADERS).status_code == 403
        machine = client.post("/_looprd/components/social/keys", headers=ADMIN,
            json={"label": "producer", "scopes": ["notify"], "service": "demo"}).json()
        (settings.secrets / "service").write_text(machine["apiKey"])
        assert client.get(BASE + "/call", headers=HEADERS).json() == {"ok": True}
        target["handlers"][0]["contract"]["version"] = 2
        path.write_text(yaml.safe_dump(target))
        client.post("/_looprd/reconcile", headers=ADMIN)
        assert client.app.state.runtime.active["social"].spec.handlers[0].contract.version == 2
        assert client.get(BASE + "/call", headers=HEADERS).status_code == 503
        assert client.get(BASE + "/items", headers=HEADERS).status_code == 200

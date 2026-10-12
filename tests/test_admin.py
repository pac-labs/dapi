"""First-start management credentials, recovery, and the panel boundary."""

import json

import pytest
from starlette.testclient import TestClient

from looprd.admin import authorized, provision, read_digest
from looprd.app import create_app
from test_http import ADMIN


def test_bootstrap_prints_once_retains_hash_and_rotates(setup_project, capsys):
    settings, _, _ = setup_project
    (settings.secrets / "admin-token").unlink()
    with TestClient(create_app(settings)) as client:
        output = capsys.readouterr().out
        token = next(line for line in output.splitlines() if line.startswith("lra_"))
        headers = {"Authorization": "Bearer " + token}
        assert client.get("/_looprd/status", headers=headers).status_code == 200
        assert client.get("/_looprd/status", headers=ADMIN).status_code == 401
        stored = (settings.data / "admin-key.json").read_text()
        assert token not in stored and len(json.loads(stored)["sha256"]) == 64
    with TestClient(create_app(settings)) as client:
        assert "lra_" not in capsys.readouterr().out
        assert client.get("/_looprd/status", headers=headers).status_code == 200
        replacement = provision(settings, rotate=True)
        assert client.get("/_looprd/status", headers=headers).status_code == 401
        assert client.get("/_looprd/status", headers={"Authorization": "Bearer " + replacement}).status_code == 200


def test_mounted_credential_precedence_and_disabled_bootstrap(setup_project, capsys):
    settings, _, _ = setup_project
    assert provision(settings) is None
    assert not (settings.data / "admin-key.json").exists()
    with pytest.raises(ValueError, match="mounted"):
        provision(settings, rotate=True)
    (settings.secrets / "admin-token").unlink()
    generated = provision(settings)
    (settings.secrets / "admin-token").write_text("admin")
    assert authorized(settings, "Bearer admin")
    assert not authorized(settings, "Bearer " + generated)
    (settings.secrets / "admin-token").write_text("")
    with pytest.raises(ValueError):
        provision(settings)
    (settings.secrets / "admin-token").unlink()
    (settings.data / "admin-key.json").unlink()
    settings.bootstrap_admin = False
    with TestClient(create_app(settings)) as client:
        assert "lra_" not in capsys.readouterr().out
        assert client.get("/_looprd/status", headers=ADMIN).status_code == 503


def test_corrupt_record_never_replaces_credential(setup_project):
    settings, _, _ = setup_project
    (settings.secrets / "admin-token").unlink()
    provision(settings)
    path = settings.data / "admin-key.json"
    path.write_text('{"version":1,"sha256":"bad"}')
    with pytest.raises(ValueError):
        provision(settings)
    assert "bad" in path.read_text()
    assert provision(settings, rotate=True)
    assert len(read_digest(settings)) == 64


def test_panel_shell_contains_no_credentials_and_management_stays_protected(setup_project):
    settings, _, _ = setup_project
    with TestClient(create_app(settings)) as client:
        panel = client.get("/_looprd/panel/")
        assert panel.status_code == 200 and panel.headers["cache-control"] == "no-store"
        assert "frame-ancestors 'none'" in panel.headers["content-security-policy"]
        assert 'nonce="' in panel.text and 'nonce="NONCE"' not in panel.text
        assert "localStorage" in panel.text and "sessionStorage" not in panel.text
        assert "Remember on this browser" in panel.text and "Forget key" in panel.text
        assert "localStorage.removeItem(credentialStorageKey)" in panel.text
        assert "test-token" not in panel.text
        assert client.get("/_looprd/status").status_code == 401
        assert client.post("/_looprd/reconcile").status_code == 401
        component = client.get("/_looprd/status", headers=ADMIN).json()["components"]["demo"]
        assert component["authMode"] == "bearer" and component["scopes"] == []


def test_cli_key_generation_and_explicit_rotation(setup_project, monkeypatch, capsys):
    from looprd.__main__ import main
    settings, _, _ = setup_project
    (settings.secrets / "admin-token").unlink()
    monkeypatch.setenv("LOOPRD_DATA", str(settings.data))
    monkeypatch.setenv("LOOPRD_SECRETS", str(settings.secrets))
    monkeypatch.setattr("sys.argv", ["looprd", "admin-key"])
    main()
    token = next(line for line in capsys.readouterr().out.splitlines() if line.startswith("lra_"))
    assert authorized(settings, "Bearer " + token)
    main()
    assert "lra_" not in capsys.readouterr().out
    monkeypatch.setattr("sys.argv", ["looprd", "admin-key", "--rotate"])
    main()
    replacement = next(line for line in capsys.readouterr().out.splitlines() if line.startswith("lra_"))
    assert replacement != token
    assert not authorized(settings, "Bearer " + token)
    assert authorized(settings, "Bearer " + replacement)

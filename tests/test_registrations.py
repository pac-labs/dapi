"""DESIGN / Generic optional service registrations: opaque, scoped, non-blocking."""

import hashlib
import json
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError
from starlette.testclient import TestClient

from looprd.app import create_app
from looprd.discovery_source import SourceSnapshot
from looprd.spec import Specification
from test_http import ADMIN

KEY = "registration-reader-proof-" + "a" * 32
URL = "/_looprd/registrations"


def setup(settings, write_spec, spec_data):
    settings.registration_readers = json.dumps({"atrium": "registration-reader"})
    (settings.secrets / "registration-reader").write_text(KEY)
    spec_data["registrations"] = [{"service": "atrium", "document": "metadata/atriumd.yaml"}]
    write_spec(spec_data)
    return {"Authorization": "Bearer " + KEY}


def test_opaque_registration_reader_auth_and_no_required_consumer(setup_project, spec_data):
    settings, write_spec, _ = setup_project
    headers = setup(settings, write_spec, spec_data)
    settings.public_url = "https://api.example.org"
    root = Path(settings.sources).parent
    (root / "metadata").mkdir()
    # The consumer owns validation; the host does not know Atrium's schema.
    content = "arbitrary: consumer-owned\n"
    (root / "metadata/atriumd.yaml").write_bytes(content.encode())
    with TestClient(create_app(settings)) as client:
        runtime = client.app.state.runtime
        assert "demo" in runtime.active and not runtime.errors
        for auth in ({}, ADMIN, {"Authorization": "Bearer test-token"}):
            assert client.get(URL, headers=auth).status_code == 401
        result = client.get(URL, headers=headers)
        assert result.status_code == 200
        body = result.json()
        assert body["complete"] and body["service"] == "atrium"
        item = body["registrations"][0]
        assert item["apiPath"] == "/looprd/demo/v1"
        assert item["apiBaseUrl"] == "https://api.example.org/looprd/demo/v1"
        assert item["source"]["kind"] == "local" and item["source"]["revision"] is None
        assert KEY not in result.text and "credentialSecret" not in result.text
        assert str(root) not in result.text
        path = item["documentPath"]
        assert client.get(path, headers=headers).status_code == 409
        document = client.get(path, params={"generation": item["generation"]}, headers=headers)
        assert document.status_code == 200
        assert document.json()["content"] == content
        assert document.json()["sha256"] == hashlib.sha256(content.encode()).hexdigest()
        assert document.json()["consistency"] == "best-effort"
        assert client.get(path.replace("/atrium/", "/another/"), headers=headers).status_code == 403
        assert client.get(path.replace("demo", "unknown"), headers=headers).status_code == 404
        (root / "metadata/atriumd.yaml").unlink()
        assert client.get(path, params={"generation": item["generation"]}, headers=headers).status_code == 503
        assert "demo" in runtime.active and not runtime.errors


@pytest.mark.parametrize("path", ["../secret", "/secret", "a/../../x", "https://elsewhere/x",
    "a?ref=x", "a#x", "a%2fx", "a\\x", ".env", "foo/.git/config", "a//x", "a b"])
def test_invalid_registration_document_paths(spec_data, path):
    spec_data["registrations"] = [{"service": "any-service", "document": path}]
    with pytest.raises(ValidationError):
        Specification.model_validate(spec_data)


def test_legacy_serialization_and_duplicate_validation(spec_data):
    legacy = Specification.model_validate(spec_data)
    assert "registrations" not in legacy.model_dump()
    explicit = {**spec_data, "registrations": []}
    assert Specification.model_validate(explicit).model_dump_json() == legacy.model_dump_json()
    registration = {"service": "unrecognized-service", "document": "own/file.txt"}
    assert Specification.model_validate({**spec_data, "registrations": [registration]}).registrations
    with pytest.raises(ValidationError, match="duplicate registration"):
        Specification.model_validate({**spec_data, "registrations": [registration, registration]})


def test_live_updates_withdrawal_outage_and_restart(setup_project, spec_data):
    settings, write_spec, write_sources = setup_project
    headers = setup(settings, write_spec, spec_data)
    with TestClient(create_app(settings)) as client:
        first = client.get(URL, headers=headers).json()["registrations"][0]
        # Document does not need to exist for declaration or API activation.
        assert "demo" in client.app.state.runtime.active
        settings.registration_readers = "invalid JSON"
        assert client.get(URL, headers=headers).status_code == 503
        assert client.get("/_looprd/health").status_code == 200
        settings.registration_readers = json.dumps({"atrium": "registration-reader"})
    # Restart restores component declarations before a successful source read.
    root = Path(settings.sources).parent
    missing = root / "looprd.yaml"
    missing.rename(root / "temporarily-offline.yaml")
    with TestClient(create_app(settings)) as client:
        report = client.get(URL, headers=headers).json()
        assert not report["complete"] and report["registrations"][0]["generation"] == first["generation"]
        assert report["registrations"][0]["source"] is None
        (root / "temporarily-offline.yaml").rename(missing)
        spec_data["registrations"] = []
        write_spec(spec_data)
        client.post("/_looprd/reconcile", headers=ADMIN)
        report = client.get(URL, headers=headers).json()
        assert report["complete"] and report["registrations"] == []
        headers = setup(settings, write_spec, spec_data)
        client.post("/_looprd/reconcile", headers=ADMIN)
        assert client.get(URL, headers=headers).json()["registrations"]
        write_sources([])
        client.post("/_looprd/reconcile", headers=ADMIN)
        assert client.get(URL, headers=headers).json()["registrations"] == []


@pytest.mark.parametrize("git", [True, False])
def test_pinned_remote_document_and_stale_generation(setup_project, spec_data, git):
    settings, write_spec, _ = setup_project
    headers = setup(settings, write_spec, spec_data)
    with TestClient(create_app(settings)) as client:
        runtime = client.app.state.runtime
        generation = runtime.active["demo"]
        sha = "a" * 40
        location = "https://api.github.com/repos/example/repo/contents/looprd.yaml?ref=" + sha if git else "https://metadata.example.org/looprd.yaml"
        snapshot = SourceSnapshot(location, sha if git else "content", generation.digest)
        runtime.reader.snapshots["demo"] = snapshot
        runtime.revisions["demo"] = snapshot.revision
        seen = []

        async def read(location, **kwargs):
            seen.append(location)
            assert kwargs["max_bytes"] == 65536
            return "consumer: configuration"

        runtime.reader.read = read
        path = URL + "/demo/atrium/document"
        result = client.get(path, params={"generation": generation.digest}, headers=headers)
        assert result.status_code == 200
        assert result.json()["consistency"] == ("git-pinned" if git else "best-effort")
        expected = "https://api.github.com/repos/example/repo/contents/metadata/atriumd.yaml?ref=" + sha if git else "https://metadata.example.org/metadata/atriumd.yaml"
        assert seen == [expected]
        assert result.json()["source"]["revision"] == (sha if git else None)

        async def changed(location, **kwargs):
            runtime.revisions["demo"] = "b" * 40
            return "stale"

        runtime.reader.read = changed
        assert client.get(path, params={"generation": generation.digest}, headers=headers).status_code == 409
        assert client.get(path, params={"generation": "old"}, headers=headers).status_code == 409


def test_read_limits_configuration_isolation_and_local_escape(setup_project, spec_data):
    settings, write_spec, _ = setup_project
    headers = setup(settings, write_spec, spec_data)
    with TestClient(create_app(settings)) as client:
        runtime = client.app.state.runtime
        generation = runtime.active["demo"]
        path = URL + "/demo/atrium/document"
        runtime.registration_reads = 4
        assert client.get(path, params={"generation": generation.digest}, headers=headers).status_code == 429
        runtime.registration_reads = 0

        async def oversized(*args, **kwargs):
            return "x" * 65537

        runtime.reader.read = oversized
        assert client.get(path, params={"generation": generation.digest}, headers=headers).status_code == 503
        assert runtime.registration_reads == 0
        settings.registration_readers = json.dumps({"atrium": "registration-reader", "another": "registration-reader"})
        assert client.get(URL, headers=headers).status_code == 401
        settings.registration_readers = json.dumps({"atrium": "../../unavailable"})
        assert client.get(URL, headers=headers).status_code == 401
        assert "demo" in runtime.active


def test_nested_registration_extension_keeps_one_component(setup_project, spec_data):
    settings, write_spec, _ = setup_project
    headers = setup(settings, write_spec, spec_data)
    spec_data["includes"] = ["extensions/looprd.yaml"]
    folder = Path(settings.sources).parent / "extensions"
    folder.mkdir()
    child = folder / "looprd.yaml"
    child.write_text(yaml.safe_dump({"registrations": [{"service": "other", "document": "metadata/other.yaml"}]}))
    write_spec(spec_data)
    settings.registration_readers = json.dumps({"atrium": "registration-reader", "other": "other-reader"})
    (settings.secrets / "other-reader").write_text("other-reader-" + "b" * 32)
    with TestClient(create_app(settings)) as client:
        other = {"Authorization": "Bearer " + "other-reader-" + "b" * 32}
        assert len(client.get(URL, headers=headers).json()["registrations"]) == 1
        report = client.get(URL, headers=other).json()
        assert report["registrations"][0]["component"] == "demo"
        assert report["registrations"][0]["document"] == "metadata/other.yaml"
        previous = client.app.state.runtime.active["demo"]
        child.write_text(yaml.safe_dump({"registrations": [{"service": "atrium", "document": "duplicate.yaml"}]}))
        client.post("/_looprd/reconcile", headers=ADMIN)
        assert client.app.state.runtime.active["demo"] is previous
        assert not client.get(URL, headers=headers).json()["complete"]

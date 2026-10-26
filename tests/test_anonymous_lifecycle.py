"""Public reads fail closed and follow normal generation/storage lifecycle."""

import copy

import pytest
import yaml
from starlette.testclient import TestClient

from looprd.app import create_app
from looprd.anonymous_read import MAX_RESPONSE, bounded_json
from test_anonymous_read import PUBLIC, public_spec, assert_public
from test_availability import set_blocked
from test_database_auth import mint
from test_http import ADMIN, BASE


def test_response_bound_is_utf8_json_not_characters():
    assert len(bounded_json("a" * (MAX_RESPONSE - 2)).body) == MAX_RESPONSE
    for value in ("a" * (MAX_RESPONSE - 1), "\u00e9" * (MAX_RESPONSE // 2), float("nan"), object()):
        with pytest.raises((ValueError, TypeError)):
            bounded_json(value)


@pytest.mark.parametrize("source", [
    "async def handle(ctx):\n    raise RuntimeError('postgres://private:secret@internal/database')",
    "async def handle(ctx):\n    return 'x' * 262144",
    "async def handle(ctx):\n    return float('nan')",
    "async def handle(ctx):\n    return object()",
    "import asyncio\nasync def handle(ctx):\n    await asyncio.sleep(10)",
    "import sqlalchemy as sa\nasync def handle(ctx):\n    with ctx.session_factory() as s:\n        s.execute(sa.text('SELECT * FROM missing_table'))",
])
def test_failures_sanitized_no_snapshot_or_preflight_execution(setup_project, spec_data, source):
    settings, write, _ = setup_project
    settings.allow_python = True
    settings.request_seconds = .05
    spec = public_spec(spec_data)
    spec["handlers"][0]["source"] = source
    write(spec)
    with TestClient(create_app(settings)) as client:
        assert_public(client.options(PUBLIC))
        response = client.get(PUBLIC)
        assert_public(response, 503)
        assert response.json() == {"error": "anonymous read unavailable"}
        assert "private" not in response.text and "missing_table" not in response.text
        assert_public(client.get(PUBLIC), 503)


def test_storage_rate_limit_and_inflight_bounds(setup_project, spec_data, monkeypatch):
    from looprd.outbox import EventError
    settings, write, _ = setup_project
    settings.allow_python = True
    spec = public_spec(spec_data)
    spec["limits"] = {"maxInFlight": 1}
    write(spec)
    with TestClient(create_app(settings)) as client:
        generation = client.app.state.runtime.active["demo"]
        storage = generation.storage
        generation.storage = None
        assert_public(client.get(PUBLIC), 503)
        assert_public(client.options(PUBLIC))
        generation.storage = storage
        generation.http_inflight = 1
        assert_public(client.get(PUBLIC), 429)
        generation.http_inflight = 0
        async def limited(*args, **kwargs):
            raise EventError(429, "private limiter detail")
        monkeypatch.setattr(generation.limiter, "check", limited)
        assert_public(client.get(PUBLIC), 429)
        assert generation.active_requests == generation.http_inflight == 0


def test_reload_rollback_removal_block_restart_and_opt_out(setup_project, spec_data):
    settings, write, sources = setup_project
    settings.allow_python = True
    spec = public_spec(spec_data)
    write(spec)
    with TestClient(create_app(settings)) as client:
        _, writer = mint(client, ["items:write"])
        client.post(BASE + "/items", headers=writer, json={"name": "retained"})
        assert client.get(PUBLIC).json()["names"] == ["retained"]
        invalid = copy.deepcopy(spec)
        invalid["handlers"][0]["method"] = "DELETE"
        write(invalid)
        assert client.post("/_looprd/reconcile", headers=ADMIN).json()["errors"]
        assert_public(client.get(PUBLIC))
        write(spec)
        set_blocked(client, True)
        assert_public(client.get(PUBLIC), 503)
        assert_public(client.options(PUBLIC), 503)
    with TestClient(create_app(settings)) as client:
        assert_public(client.get(PUBLIC), 503)
        set_blocked(client, False)
        assert client.get(PUBLIC).json()["names"] == ["retained"]
        sources([])
        client.post("/_looprd/reconcile", headers=ADMIN)
        assert client.get(PUBLIC).status_code == 404
        sources()
        client.post("/_looprd/reconcile", headers=ADMIN)
        assert client.get(PUBLIC).json()["names"] == ["retained"]
        spec["handlers"][0].pop("access")
        spec["handlers"][0]["scopes"] = ["items:read"]
        write(spec)
        assert not client.post("/_looprd/reconcile", headers=ADMIN).json()["errors"]
        assert client.get(PUBLIC).status_code == 401
    with TestClient(create_app(settings)) as client:
        assert client.get(PUBLIC).status_code == 401
        _, reader = mint(client, ["items:read"])
        assert client.get(PUBLIC, headers=reader).json()["names"] == ["retained"]


def test_nested_mounts_use_child_database_and_no_top_level_alias(setup_project, spec_data):
    settings, write, _ = setup_project
    settings.allow_python = True
    parent = copy.deepcopy(spec_data)
    parent["subcomponents"] = [{"source": "child/looprd.yaml"}]
    child = public_spec(spec_data)
    child["component"] = "child"
    path = settings.data.parent / "child/looprd.yaml"
    path.parent.mkdir()
    path.write_text(yaml.safe_dump(child), encoding="utf-8")
    write(parent)
    with TestClient(create_app(settings)) as client:
        for prefix in ("/looprd/demo/child/v1", "/dapi/demo/child/v1"):
            assert_public(client.get(prefix + "/catalog/public"))
            assert client.get(prefix + "/items").status_code == 401
        assert client.get(PUBLIC).status_code == 401
        assert client.get("/looprd/child/v1/catalog/public").status_code == 404

"""DESIGN / Anonymous read handlers: explicit public projections, private defaults."""

import pytest
from pydantic import ValidationError
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from looprd.app import create_app
from looprd.spec import Specification
from test_database_auth import database_auth, mint
from test_http import ADMIN, BASE

CAPABILITY = "auth.anonymous-read-handler.v1"
PUBLIC = BASE + "/catalog/public"
SOURCE = '''
import sqlalchemy as sa
async def handle(ctx):
    with ctx.session_factory() as session:
        names = session.execute(sa.text('SELECT name FROM "Item" ORDER BY id')).scalars().all()
    return {"names": names, "kind": ctx.principal.kind,
            "scopes": sorted(ctx.principal.scopes), "subject": ctx.principal.subject,
            "authorization": ctx.request.headers.get("authorization"),
            "cookies": ctx.request.cookies}
'''


def public_spec(spec):
    database_auth(spec)
    spec["requires"] = [CAPABILITY]
    spec["cors"] = {"allowedOrigins": ["https://client.example.com"]}
    spec["handlers"] = [
        {"path": "/catalog/public", "kind": "python", "access": "anonymous", "source": SOURCE},
        {"path": "/catalog/public", "method": "POST", "scopes": ["items:write"], "response": "private"},
        {"path": "/private", "scopes": ["items:read"], "response": "private"},
    ]
    return spec


def assert_public(response, status=200):
    assert response.status_code == status, response.text
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["access-control-allow-origin"] == "*"
    assert response.headers["content-type"] == "application/json"
    assert "access-control-allow-credentials" not in response.headers
    assert "set-cookie" not in response.headers


def test_owning_database_anonymous_identity_and_private_boundaries(setup_project, spec_data, monkeypatch):
    settings, write, _ = setup_project
    settings.allow_python = True
    write(public_spec(spec_data))
    app = create_app(settings)
    with TestClient(app) as client:
        assert not app.state.runtime.errors
        _, reader = mint(client, ["items:read"])
        _, writer = mint(client, ["items:write"])
        assert client.post(BASE + "/items", headers=writer, json={"name": "real data"}).status_code == 201
        for headers in ({}, reader, writer, ADMIN, {"Authorization": "Bearer invalid"}):
            response = client.get(PUBLIC, headers={**headers, "Cookie": "session=private",
                                  "Origin": "https://unlisted.example.com"})
            assert_public(response)
            assert response.json() == {"names": ["real data"], "kind": "anonymous", "scopes": [],
                                       "subject": None, "authorization": None, "cookies": {}}
        for path in ("/private", "/items", "/openapi.json", "/_events/items", "/_sync/items", "/_push",
                     "/_jobs/example", "/_services"):
            assert client.get(BASE + path).status_code == 401
        assert client.post(PUBLIC).status_code == 401
        assert client.post(PUBLIC, headers=reader).status_code == 403
        assert client.post(PUBLIC, headers=writer).json() == "private"
        assert client.post(BASE + "/_ws-ticket").status_code == 401
        assert client.get("/_looprd/status").status_code == 401
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(BASE + "/events"):
                pass
        document = client.get(BASE + "/openapi.json", headers=reader).json()
        assert document["security"] == [{"bearer": []}]
        assert document["paths"]["/catalog/public"]["get"]["security"] == []
        assert "security" not in document["paths"]["/catalog/public"]["post"]
        generation = app.state.runtime.active["demo"]
        async def forbidden(*args, **kwargs):
            raise AssertionError("public GET must not authenticate")
        monkeypatch.setattr(generation, "authenticate", forbidden)
        assert_public(client.get(PUBLIC))


def test_exact_aliases_cors_preflight_and_methods(setup_project, spec_data):
    settings, write, _ = setup_project
    settings.allow_python = True
    write(public_spec(spec_data))
    with TestClient(create_app(settings)) as client:
        _, reader = mint(client, ["items:read"])
        for prefix in ("/looprd/demo/v1", "/dapi/demo/v1"):
            url = prefix + "/catalog/public"
            assert_public(client.get(url))
            for suffix in ("/", "/extra"):
                assert client.get(url + suffix, headers=reader, follow_redirects=False).status_code == 404
            for method in ("HEAD", "PUT", "PATCH", "DELETE"):
                assert client.request(method, url).status_code == 404
            for headers in ({}, {"Origin": "null", "Access-Control-Request-Method": "GET",
                                 "Access-Control-Request-Headers": "content-type, authorization"}):
                response = client.options(url, headers=headers)
                assert_public(response)
                assert response.headers["access-control-allow-methods"] == "GET"
                assert response.headers["access-control-max-age"] == "0"
            for headers in ({"Access-Control-Request-Method": "PUT"},
                            {"Access-Control-Request-Headers": "x-private"},
                            {"Access-Control-Request-Headers": "a" * 1025},
                            {"Access-Control-Request-Headers": ",".join(["accept"] * 17)}):
                assert_public(client.options(url, headers=headers), 400)
        private = {"Origin": "https://client.example.com", "Access-Control-Request-Method": "POST"}
        response = client.options(PUBLIC, headers=private)
        # POST belongs to the private route, including its preflight.
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == private["Origin"]
        denied = {**private, "Origin": "https://unlisted.example.com"}
        assert client.options(PUBLIC, headers=denied).status_code == 400
        assert client.get(BASE + "/private", headers={**reader, "Origin": private["Origin"]}).status_code == 200
        assert client.get(BASE + "/private", headers={"Origin": private["Origin"]}).status_code == 401
        assert client.get("/looprd/other/v1/catalog/public").status_code == 404


@pytest.mark.parametrize("change", [
    {"method": "POST"}, {"scopes": ["items:read"]}, {"access": "public"},
    {"path": "/"}, {"path": "/catalog/public/"}, {"path": "/catalog/*"},
    {"execution": "process"}, {"status": 201}, {"path": "/items"},
    {"contract": {"name": "catalog", "version": 1, "request": {}, "response": {}}},
    {"path": "/openapi.json"}, {"path": "/events"},
])
def test_invalid_declarations(spec_data, change):
    spec = public_spec(spec_data)
    spec["handlers"][0].update(change)
    with pytest.raises(ValidationError):
        Specification.model_validate(spec)


@pytest.mark.parametrize("mode", ["public", "bearer", "verified", "missing-capability", "prefix", "slash-alias"])
def test_explicit_capability_storage_and_no_ambiguous_prefixes(spec_data, mode):
    spec = public_spec(spec_data)
    if mode == "missing-capability":
        spec["requires"] = []
    elif mode == "prefix":
        spec["handlers"].append({"path": "/catalog", "scopes": ["items:read"]})
    elif mode == "slash-alias":
        spec["handlers"][1]["path"] += "/"
    else:
        spec["auth"]["mode"] = mode
    with pytest.raises(ValidationError):
        Specification.model_validate(spec)


def test_legacy_serialization_and_python_trust_gate(setup_project, spec_data):
    settings, write, _ = setup_project
    spec_data["handlers"] = [{"path": "/old", "response": "legacy"}]
    parsed = Specification.model_validate(spec_data)
    assert "access" not in parsed.model_dump()["handlers"][0]
    assert "access" not in parsed.model_dump_json()
    write(public_spec(spec_data))
    with TestClient(create_app(settings)) as client:
        assert "demo" not in client.app.state.runtime.active
        assert client.get(PUBLIC).status_code == 404

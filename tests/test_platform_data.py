"""Opt-in constraints, bounded queries, projections and revision conflicts."""

import copy

from starlette.testclient import TestClient

from looprd.app import create_app
from test_http import BASE, HEADERS, ADMIN


def test_filters_keyset_projection_and_revision(setup_project, spec_data):
    settings, write_spec, _ = setup_project
    spec_data["models"]["Item"]["fields"]["revision"] = {"type": "integer", "required": True}
    spec_data["resources"]["items"].update(revisionField="revision", query={"filters": {"name": ["eq", "prefix"]}, "sort": ["name"]})
    spec_data["resources"]["names"] = {"model": "Item", "path": "/names", "operations": ["list", "get"], "projection": ["id", "name"]}
    write_spec(spec_data)
    with TestClient(create_app(settings)) as client:
        for name in ("aa", "ab", "ac", "ba"):
            response = client.post(BASE + "/items", json={"name": name}, headers=HEADERS)
            assert response.status_code == 201, response.text
            assert response.headers["etag"] == '"1"'
        params = {"filter.name.prefix": "a", "sort": "name", "pagination": "cursor", "limit": 2}
        page = client.get(BASE + "/items", params=params, headers=HEADERS).json()
        assert [item["name"] for item in page["items"]] == ["aa", "ab"] and page["hasMore"]
        params["cursor"] = page["cursor"]
        assert [item["name"] for item in client.get(BASE + "/items", params=params, headers=HEADERS).json()["items"]] == ["ac"]
        params["filter.name.prefix"] = "b"
        assert client.get(BASE + "/items", params=params, headers=HEADERS).status_code == 400
        assert client.get(BASE + "/items?filter.name.sql=drop", headers=HEADERS).status_code == 422
        assert set(client.get(BASE + "/names/1", headers=HEADERS).json()) == {"id", "name"}
        assert client.patch(BASE + "/items/1", json={"name": "new"}, headers=HEADERS).status_code == 428
        response = client.patch(BASE + "/items/1", json={"name": "new"}, headers={**HEADERS, "If-Match": '"1"'})
        assert response.status_code == 200 and response.json()["revision"] == 2
        assert client.delete(BASE + "/items/1", headers={**HEADERS, "If-Match": '"1"'}).status_code == 412
        assert client.delete(BASE + "/items/1", headers={**HEADERS, "If-Match": '"2"'}).status_code == 204


def test_composite_unique_foreign_keys_indexes_and_bad_migration_preserve_data(setup_project, spec_data):
    settings, write_spec, _ = setup_project
    spec_data["models"]["Container"] = {"fields": {"id": {"type": "integer", "primaryKey": True, "generated": True}}}
    spec_data["models"]["Item"]["fields"]["container"] = {"type": "integer", "required": True, "references": {"model": "Container", "field": "id"}}
    spec_data["models"]["Item"]["fields"]["name"]["unique"] = False
    spec_data["models"]["Item"].update(unique=[["container", "name"]], indexes=[{"name": "idx_item_container", "fields": ["container"]}])
    spec_data["resources"]["containers"] = {"model": "Container", "path": "/containers", "operations": ["create", "delete"]}
    write_spec(spec_data)
    with TestClient(create_app(settings)) as client:
        container = client.post(BASE + "/containers", json={}, headers=HEADERS).json()["id"]
        assert client.post(BASE + "/items", json={"name": "one", "container": container}, headers=HEADERS).status_code == 201
        assert client.post(BASE + "/items", json={"name": "one", "container": container}, headers=HEADERS).status_code == 409
        assert client.post(BASE + "/items", json={"name": "two", "container": 999}, headers=HEADERS).status_code == 409
        assert client.delete(BASE + "/containers/1", headers=HEADERS).status_code == 409
        previous = client.app.state.runtime.active["demo"]
        spec_data["models"]["Item"]["indexes"].append({"name": "idx_new", "fields": ["name"]})
        write_spec(spec_data)
        client.post("/_looprd/reconcile", headers=ADMIN)
        assert client.app.state.runtime.active["demo"] is previous
        assert len(client.get(BASE + "/items", headers=HEADERS).json()) == 1

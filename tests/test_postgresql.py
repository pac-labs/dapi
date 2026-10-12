"""A real PostgreSQL integration check; CI provides an ephemeral service."""

import os

import pytest
from starlette.testclient import TestClient

from looprd.app import create_app
from test_http import ADMIN, BASE
from test_database_auth import database_auth, mint, KEYS


@pytest.mark.skipif(not os.getenv("LOOPRD_TEST_POSTGRES_URL"), reason="PostgreSQL service not configured")
def test_real_postgresql_crud(setup_project, spec_data):
    settings, write_spec, _ = setup_project
    (settings.secrets / "demo/database").write_text(os.environ["LOOPRD_TEST_POSTGRES_URL"], encoding="utf-8")
    spec_data["database"] = {"backend": "postgresql", "schemaVersion": 1, "connectionSecret": "demo/database"}
    database_auth(spec_data)
    write_spec(spec_data)
    with TestClient(create_app(settings)) as client:
        key, headers = mint(client, ["items:read", "items:write"])
        response = client.post(BASE + "/items", headers=headers, json={"name": "postgres"})
        assert response.status_code == 201
        record_id = response.json()["id"]
        assert client.get(BASE + f"/items/{record_id}", headers=headers).json()["name"] == "postgres"
        spec_data["models"]["Item"]["fields"]["extra"] = {"type": "boolean"}
        spec_data["database"].update(schemaVersion=2, migrations=[{
            "version": 2, "statements": ['ALTER TABLE "Item" ADD COLUMN extra BOOLEAN'],
        }])
        write_spec(spec_data)
        status = client.post("/_looprd/reconcile", headers=ADMIN)
        assert not status.json()["errors"]
        assert client.patch(BASE + f"/items/{record_id}", headers=headers, json={"extra": True}).json()["extra"] is True
        assert client.delete(BASE + f"/items/{record_id}", headers=headers).status_code == 204
        assert client.get(KEYS, headers=ADMIN).json()["keys"][0]["id"] == key["id"]
        assert client.delete(KEYS + "/" + key["id"], headers=ADMIN).status_code == 204
        assert client.get(BASE + "/items", headers=headers).status_code == 401

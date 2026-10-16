"""Real PostgreSQL device proof, rollback, coalescing and snapshot compatibility."""

import os
import secrets

import pytest
import sqlalchemy as sa
from starlette.testclient import TestClient

from looprd.app import create_app
from test_push import push_project, activate, store, owner, enqueue
from test_database_auth import mint
from test_http import BASE


@pytest.mark.skipif(not os.getenv("LOOPRD_TEST_POSTGRES_URL"), reason="PostgreSQL service not configured")
def test_postgres_push_atomicity_and_snapshot(push_project, setup_project, spec_data):
    schema = "push_" + secrets.token_hex(8)
    base = sa.engine.make_url(os.environ["LOOPRD_TEST_POSTGRES_URL"]).set(drivername="postgresql+psycopg")
    admin = sa.create_engine(base)
    with admin.begin() as connection:
        connection.exec_driver_sql('CREATE SCHEMA "' + schema + '"')
    url = base.update_query_dict({"options": "-csearch_path=" + schema})
    (push_project.secrets / "pg").write_text(url.render_as_string(hide_password=False))
    spec_data["database"] = {"backend": "postgresql", "connectionSecret": "pg"}
    setup_project[1](spec_data)
    try:
        with TestClient(create_app(push_project)) as client:
            _, headers = mint(client, ["push:manage", "items:read", "items:write", "events:read"])
            activate(client, headers)
            with pytest.raises(RuntimeError):
                with store(client).storage.events.transaction() as session:
                    store(client).notify(session, owner(client, headers))
                    raise RuntimeError("rollback")
            assert store(client).status()["queued"] == 0
            enqueue(client, headers)
            first = store(client).claim()
            enqueue(client, headers)
            store(client).finish(first, 200)
            assert store(client).status()["queued"] == 1
            client.post(BASE + "/items", headers=headers, json={"name": "postgres"})
            page = client.get(BASE + "/_sync/items", headers=headers).json()
            assert page["complete"] and page["items"][0]["data"]["name"] == "postgres"
    finally:
        with admin.begin() as connection:
            connection.exec_driver_sql('DROP SCHEMA "' + schema + '" CASCADE')
        admin.dispose()

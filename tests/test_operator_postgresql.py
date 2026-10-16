"""Real PostgreSQL operator receipts, atomic rollback and durable notifications."""

import os
import secrets

import pytest
import sqlalchemy as sa
from starlette.testclient import TestClient

from looprd.app import create_app
from test_administration import managed, insert, intent, ROOT, ROWS, ACTION
from test_http import ADMIN, BASE, HEADERS


@pytest.mark.skipif(not os.getenv("LOOPRD_TEST_POSTGRES_URL"), reason="PostgreSQL service not configured")
def test_postgres_operator_transaction_and_durable_outbox(managed):
    settings, write, _, spec = managed
    schema = "operator_" + secrets.token_hex(8)
    base = sa.engine.make_url(os.environ["LOOPRD_TEST_POSTGRES_URL"]).set(drivername="postgresql+psycopg")
    admin = sa.create_engine(base)
    with admin.begin() as connection:
        connection.exec_driver_sql('CREATE SCHEMA "' + schema + '"')
    url = base.update_query_dict({"options": "-csearch_path=" + schema})
    (settings.secrets / "pg").write_text(url.render_as_string(hide_password=False))
    spec["database"] = {"backend": "postgresql", "connectionSecret": "pg"}
    spec["realtime"] = {"delivery": "durable"}
    write(spec)
    try:
        with TestClient(create_app(settings)) as client:
            identity = insert(client)
            payload = intent(client, identity, {"amount": 5})
            path = ROWS + "/1/actions/credit"
            assert client.post(path, headers=ADMIN, json=payload).status_code == 200
            assert client.post(path, headers=ADMIN, json=payload).json()["duplicate"] is True
            assert client.get(BASE + "/items/1", headers=HEADERS).json()["coins"] == 15
            assert len(client.get(ROOT + "/audit", headers=ADMIN).json()["entries"]) == 1
            assert len(client.get(BASE + "/_events/items", headers=HEADERS).json()["events"]) == 2
            spec["management"]["actions"]["credit"]["source"] = ACTION + "    raise RuntimeError('rollback')\n"
            write(spec)
            client.post("/_looprd/reconcile", headers=ADMIN)
            assert client.post(path, headers=ADMIN, json=intent(client, identity, {"amount": 5})).status_code == 500
            assert client.get(BASE + "/items/1", headers=HEADERS).json()["coins"] == 15
            assert len(client.get(ROOT + "/audit", headers=ADMIN).json()["entries"]) == 1
            assert len(client.get(BASE + "/_events/items", headers=HEADERS).json()["events"]) == 2
    finally:
        with admin.begin() as connection:
            connection.exec_driver_sql('DROP SCHEMA "' + schema + '" CASCADE')
        admin.dispose()

"""Disposable PostgreSQL schema exercises durable transactions, leases and ownership."""

import os
import secrets

import pytest
import sqlalchemy as sa
from starlette.testclient import TestClient

from looprd.app import create_app
from looprd.database import Storage
from looprd.spec import Specification
from test_http import HEADERS


@pytest.mark.skipif(not os.getenv("LOOPRD_TEST_POSTGRES_URL"), reason="PostgreSQL service not configured")
def test_postgres_platform_transactions_and_database_ownership(setup_project, spec_data):
    settings, write, _ = setup_project
    schema = "platform_" + secrets.token_hex(8)
    base = sa.engine.make_url(os.environ["LOOPRD_TEST_POSTGRES_URL"]).set(drivername="postgresql+psycopg")
    admin = sa.create_engine(base)
    with admin.begin() as connection:
        connection.exec_driver_sql('CREATE SCHEMA "' + schema + '"')
    url = base.update_query_dict({"options": "-csearch_path=" + schema})
    (settings.secrets / "pg").write_text(url.render_as_string(hide_password=False), encoding="utf-8")
    spec_data["database"] = {"backend": "postgresql", "connectionSecret": "pg"}
    spec_data["realtime"] = {"delivery": "durable"}
    spec_data["limits"] = {"maxRows": 1}
    spec_data["jobs"] = {"work": {"source": "async def handle(ctx): pass"}}
    settings.allow_python = True
    write(spec_data)
    try:
        with TestClient(create_app(settings)) as client:
            generation = client.app.state.runtime.active["demo"]
            events = generation.storage.events
            assert client.post("/looprd/demo/v1/items", headers=HEADERS, json={"name": "persisted"}).status_code == 201
            assert client.post("/looprd/demo/v1/items", headers=HEADERS, json={"name": "quota"}).status_code == 507
            assert len(client.get("/looprd/demo/v1/_events/items", headers=HEADERS).json()["events"]) == 1
            with pytest.raises(RuntimeError):
                with events.transaction() as session:
                    session.execute(generation.storage.tables["Item"].insert().values(name="rollback"))
                    events.emit(session, {"resource": "items", "action": "custom", "data": "rollback"})
                    raise RuntimeError("rollback")
            assert len(client.get("/looprd/demo/v1/_events/items", headers=HEADERS).json()["events"]) == 1
            jobs = generation.storage.jobs
            identity = jobs.enqueue("work", {"value": 1}, "owner", delay=100)
            with jobs.storage.sessions.begin() as session:
                session.execute(jobs.table.update().where(jobs.table.c.id == identity).values(next=0))
            first = jobs.claim("one", 1)
            assert first and jobs.claim("two", 1) is None
            jobs.finish(first, "one")
            other = dict(spec_data, component="different")
            storage = Storage(Specification.model_validate(other), settings)
            with pytest.raises(ValueError, match="owned by another"):
                storage.initialize()
        with TestClient(create_app(settings)) as client:
            assert client.get("/looprd/demo/v1/items", headers=HEADERS).json()[0]["name"] == "persisted"
            assert len(client.get("/looprd/demo/v1/_events/items", headers=HEADERS).json()["events"]) == 1
    finally:
        with admin.begin() as connection:
            connection.exec_driver_sql('DROP SCHEMA "' + schema + '" CASCADE')
        admin.dispose()

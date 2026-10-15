"""Schema validation must recognize enforced uniqueness, not DDL-parser quirks."""

import os
import secrets
import sqlite3

import pytest
import sqlalchemy as sa

from looprd.database import Storage
from looprd.spec import Specification


def legacy_database(settings, ddl, indexes=()):
    directory = settings.data / "databases"
    directory.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(directory / "demo.sqlite3") as db:
        db.execute(ddl)
        for statement in indexes:
            db.execute(statement)


def test_sqlite_inline_sized_unique_constraint_survives_activation_and_restart(setup_project, spec_data):
    settings, _, _ = setup_project
    legacy_database(settings, 'CREATE TABLE Item (id INTEGER NOT NULL PRIMARY KEY, name VARCHAR(30) NOT NULL UNIQUE)')
    for _ in range(2):
        storage = Storage(Specification.model_validate(spec_data), settings)
        try:
            storage.initialize()
            with storage.engine.begin() as connection:
                if not connection.execute(sa.select(storage.tables["Item"])).first():
                    connection.execute(storage.tables["Item"].insert().values(name="retained"))
            with pytest.raises(sa.exc.IntegrityError):
                with storage.engine.begin() as connection:
                    connection.execute(storage.tables["Item"].insert().values(name="retained"))
        finally:
            storage.close()


def test_sqlite_quoted_composite_unique_constraint_is_recognized(setup_project, spec_data):
    settings, _, _ = setup_project
    spec_data["models"]["Item"]["fields"]["name"]["unique"] = False
    spec_data["models"]["Item"]["fields"]["tag"] = {"type": "string", "maxLength": 8, "required": True}
    spec_data["models"]["Item"]["unique"] = [["name", "tag"]]
    legacy_database(settings, 'CREATE TABLE Item (id INTEGER NOT NULL PRIMARY KEY, name VARCHAR(30) NOT NULL, tag VARCHAR(8) NOT NULL, UNIQUE ([name], [tag]))')
    storage = Storage(Specification.model_validate(spec_data), settings)
    try:
        storage.initialize()
    finally:
        storage.close()


@pytest.mark.parametrize("indexes", [[], ["CREATE INDEX lookup_name ON Item(name)"],
    ["CREATE UNIQUE INDEX partial_name ON Item(name) WHERE name != 'excluded'"],
    ["CREATE UNIQUE INDEX expression_name ON Item(lower(name))"]])
def test_sqlite_does_not_accept_weaker_indexes_as_column_uniqueness(setup_project, spec_data, indexes):
    settings, _, _ = setup_project
    legacy_database(settings, 'CREATE TABLE Item (id INTEGER NOT NULL PRIMARY KEY, name VARCHAR(30) NOT NULL)', indexes)
    storage = Storage(Specification.model_validate(spec_data), settings)
    try:
        with pytest.raises(ValueError, match="unique constraint is missing"):
            storage.initialize()
    finally:
        storage.close()


def test_declared_full_index_cannot_be_satisfied_by_partial_index(setup_project, spec_data):
    settings, _, _ = setup_project
    spec_data["models"]["Item"]["indexes"] = [{"name": "lookup_name", "fields": ["name"]}]
    legacy_database(settings, 'CREATE TABLE Item (id INTEGER NOT NULL PRIMARY KEY, name VARCHAR(30) NOT NULL UNIQUE)',
                    ["CREATE INDEX lookup_name ON Item(name) WHERE name != 'excluded'"])
    storage = Storage(Specification.model_validate(spec_data), settings)
    try:
        with pytest.raises(ValueError, match="index is missing or incompatible"):
            storage.initialize()
    finally:
        storage.close()


def test_sqlite_index_metadata_quotes_reserved_tables_and_index_names(setup_project, spec_data):
    settings, _, _ = setup_project
    spec_data["models"]["Order"] = spec_data["models"].pop("Item")
    spec_data["resources"]["items"]["model"] = "Order"
    legacy_database(settings, 'CREATE TABLE "Order" (id INTEGER NOT NULL PRIMARY KEY, name VARCHAR(30) NOT NULL)',
                    ['CREATE UNIQUE INDEX "odd""index" ON "Order" (name)'])
    storage = Storage(Specification.model_validate(spec_data), settings)
    try:
        storage.initialize()
    finally:
        storage.close()


@pytest.mark.skipif(not os.getenv("LOOPRD_TEST_POSTGRES_URL"), reason="PostgreSQL service not configured")
def test_postgres_partial_index_does_not_satisfy_unique_column(setup_project, spec_data):
    settings, _, _ = setup_project
    schema = "uniqueness_" + secrets.token_hex(8)
    base = sa.engine.make_url(os.environ["LOOPRD_TEST_POSTGRES_URL"]).set(drivername="postgresql+psycopg")
    admin = sa.create_engine(base)
    with admin.begin() as connection:
        connection.exec_driver_sql('CREATE SCHEMA "' + schema + '"')
    url = base.update_query_dict({"options": "-csearch_path=" + schema})
    (settings.secrets / "pg").write_text(url.render_as_string(hide_password=False), encoding="utf-8")
    spec_data["database"] = {"backend": "postgresql", "connectionSecret": "pg"}
    storage = Storage(Specification.model_validate(spec_data), settings)
    try:
        with storage.engine.begin() as connection:
            connection.exec_driver_sql('CREATE TABLE "Item" (id INTEGER NOT NULL PRIMARY KEY, name VARCHAR(30) NOT NULL)')
            connection.exec_driver_sql('CREATE UNIQUE INDEX partial_name ON "Item" (name) WHERE name != \'excluded\'')
        with pytest.raises(ValueError, match="unique constraint is missing"):
            storage.initialize()
    finally:
        storage.close()
        with admin.begin() as connection:
            connection.exec_driver_sql('DROP SCHEMA "' + schema + '" CASCADE')
        admin.dispose()

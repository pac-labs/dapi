"""Component-local SQLAlchemy storage and explicit, transactional migrations."""

import hashlib
import json
from datetime import datetime

import sqlalchemy as sa
from pydantic import ConfigDict, Field, create_model
from sqlalchemy.orm import sessionmaker

from .spec import Specification
from .auth import KeyStore

SQL_TYPES = {
    "integer": sa.Integer, "float": sa.Float, "boolean": sa.Boolean,
    "datetime": sa.DateTime, "json": sa.JSON,
}
PY_TYPES = {
    "integer": int, "float": float, "boolean": bool,
    "datetime": datetime, "json": object, "string": str,
}


class Storage:
    def __init__(self, spec: Specification, settings):
        self.spec = spec
        self.metadata = sa.MetaData()
        self.tables = {}
        self.inputs = {}
        for name, model in spec.models.items():
            columns = []
            create_fields, update_fields = {}, {}
            for field_name, field in model.fields.items():
                sql_type = sa.String(field.maxLength) if field.type == "string" else SQL_TYPES[field.type]()
                columns.append(sa.Column(
                    field_name, sql_type, primary_key=field.primaryKey,
                    autoincrement=True if field.primaryKey else "auto",
                    nullable=not (field.required or field.primaryKey), unique=field.unique,
                ))
                if not field.generated:
                    py_type = PY_TYPES[field.type]
                    annotation = py_type if field.required else py_type | None
                    kwargs = {"max_length": field.maxLength} if field.type == "string" else {}
                    create_fields[field_name] = (annotation, Field(default=... if field.required else None, **kwargs))
                    update_fields[field_name] = (annotation, Field(default=None, **kwargs))
            self.tables[name] = sa.Table(name, self.metadata, *columns)
            config = ConfigDict(extra="forbid", strict=True)
            self.inputs[name] = (
                create_model(f"{name}Create", __config__=config, **create_fields),
                create_model(f"{name}Update", __config__=config, **update_fields),
            )
        self.marker = sa.Table(
            "_looprd_schema", sa.MetaData(),
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("version", sa.Integer, nullable=False),
            sa.Column("fingerprint", sa.String(64), nullable=False),
            sa.Column("backend", sa.String(20), nullable=False),
        )
        directory = settings.data / "databases"
        directory.mkdir(parents=True, exist_ok=True)
        self.identity_file = directory / f"{spec.component}.json"
        backend = spec.database.backend
        identity = {"backend": backend, "connectionSecret": spec.database.connectionSecret}
        if self.identity_file.exists() and json.loads(self.identity_file.read_text()) != identity:
            raise ValueError("database identity changes require explicit data migration")
        if backend == "sqlite":
            url = sa.URL.create("sqlite", database=str((directory / f"{spec.component}.sqlite3").resolve()))
            self.engine = sa.create_engine(url, connect_args={"check_same_thread": False, "timeout": 5})
            sa.event.listen(self.engine, "connect", self._sqlite_setup)
        else:
            url = sa.engine.make_url(settings.secret(spec.database.connectionSecret))
            if url.drivername not in ("postgresql", "postgresql+psycopg"):
                raise ValueError("connection secret must contain a PostgreSQL URL")
            self.engine = sa.create_engine(url.set(drivername="postgresql+psycopg"), pool_pre_ping=True)
        self.sessions = sessionmaker(bind=self.engine)
        self.keys = KeyStore(self) if spec.auth.mode == "database" else None
        self.identity = identity

    @staticmethod
    def _sqlite_setup(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")

    def initialize(self):
        fingerprint = hashlib.sha256(json.dumps(
            {name: model.model_dump() for name, model in self.spec.models.items()}, sort_keys=True,
        ).encode()).hexdigest()
        try:
            with self.engine.connect() as connection:
                # Explicit BEGIN prevents sqlite's legacy DDL autocommit behavior.
                if self.spec.database.backend == "sqlite":
                    connection.exec_driver_sql("BEGIN IMMEDIATE")
                else:
                    connection.begin()
                try:
                    self._initialize_transaction(connection, fingerprint)
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise
            self.identity_file.write_text(json.dumps(self.identity), encoding="utf-8")
        except BaseException:
            self.engine.dispose()
            raise

    def _initialize_transaction(self, connection, fingerprint):
        if self.keys:
            self.keys.table.create(connection, checkfirst=True)
        self.marker.create(connection, checkfirst=True)
        previous = connection.execute(sa.select(self.marker).where(self.marker.c.id == 1)).mappings().first()
        target = self.spec.database.schemaVersion
        if previous is None:
            self.metadata.create_all(connection)
        else:
            if target < previous["version"]:
                raise ValueError("schema downgrade is not supported")
            if target == previous["version"] and fingerprint != previous["fingerprint"]:
                raise ValueError("models changed without a schema version increase")
            for migration in self.spec.database.migrations:
                if previous["version"] < migration.version <= target:
                    for statement in migration.statements:
                        connection.exec_driver_sql(statement)
        # Detect missing/mismatched columns before activation, including incomplete migrations.
        inspector = sa.inspect(connection)
        for name, table in self.tables.items():
            columns = {c["name"]: c for c in inspector.get_columns(name)}
            if set(columns) != set(table.columns.keys()):
                raise ValueError("database columns do not match declared models")
            for column in table.columns:
                actual = columns[column.name]
                if actual["type"]._type_affinity is not column.type._type_affinity:
                    raise ValueError("database column type does not match model")
                if not column.primary_key and actual["nullable"] != column.nullable:
                    raise ValueError("database nullability does not match model")
                if isinstance(column.type, sa.String) and actual["type"].length != column.type.length:
                    raise ValueError("database string length does not match model")
            expected_pk = [column.name for column in table.primary_key.columns]
            if inspector.get_pk_constraint(name)["constrained_columns"] != expected_pk:
                raise ValueError("database primary key does not match model")
            unique_columns = {
                tuple(constraint["column_names"]) for constraint in inspector.get_unique_constraints(name)
            } | {
                tuple(index["column_names"]) for index in inspector.get_indexes(name) if index["unique"]
            }
            for column in table.columns:
                if column.unique and (column.name,) not in unique_columns:
                    raise ValueError("declared unique constraint is missing from database")
        values = {"version": target, "fingerprint": fingerprint, "backend": self.spec.database.backend}
        if previous:
            connection.execute(self.marker.update().where(self.marker.c.id == 1).values(**values))
        else:
            connection.execute(self.marker.insert().values(id=1, **values))

    def close(self):
        self.engine.dispose()

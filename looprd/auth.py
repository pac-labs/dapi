"""Component-local, hashed API keys and authenticated identities."""

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass, field

import sqlalchemy as sa


@dataclass(frozen=True)
class Principal:
    id: str
    label: str
    scopes: frozenset[str]
    kind: str = "api-key"
    realm: str | None = None
    subject: str | None = None
    session_id: str | None = None
    expires_at: float | None = None
    credential: str | None = field(default=None, repr=False, compare=False)
    key_id: str | None = field(default=None, repr=False)

    def permits(self, required):
        return set(required) <= self.scopes


class KeyStore:
    def __init__(self, storage):
        self.storage = storage
        self.table = sa.Table(
            "_looprd_api_keys", sa.MetaData(),
            sa.Column("id", sa.String(32), primary_key=True),
            sa.Column("label", sa.String(100), nullable=False),
            sa.Column("digest", sa.String(64), nullable=False),
            sa.Column("scopes", sa.JSON, nullable=False),
            sa.Column("createdAt", sa.Float, nullable=False),
            sa.Column("expiresAt", sa.Float),
            sa.Column("revokedAt", sa.Float),
            sa.Column("service", sa.String(48)),
        )

    @staticmethod
    def metadata(row):
        return {key: value for key, value in row.items() if key != "digest"}

    def initialize(self, connection):
        self.table.create(connection, checkfirst=True)
        if "service" not in {column["name"] for column in sa.inspect(connection).get_columns(self.table.name)}:
            connection.exec_driver_sql('ALTER TABLE "_looprd_api_keys" ADD COLUMN service VARCHAR(48)')

    def create(self, label, scopes, expires_in, service=None):
        now = time.time()
        key_id = secrets.token_hex(16)
        token = f"lrk_{key_id}.{secrets.token_urlsafe(32)}"
        row = dict(id=key_id, label=label, scopes=scopes, createdAt=now,
                   expiresAt=now + expires_in if expires_in is not None else None, revokedAt=None,
                   digest=hashlib.sha256(token.encode()).hexdigest(), service=service)
        with self.storage.sessions.begin() as session:
            session.execute(self.table.insert().values(**row))
        return {**self.metadata(row), "apiKey": token}

    def list(self):
        columns = [c for c in self.table.columns if c.name != "digest"]
        with self.storage.sessions() as session:
            return [dict(row) for row in session.execute(sa.select(*columns).order_by(self.table.c.createdAt)).mappings()]

    def revoke(self, key_id):
        with self.storage.sessions.begin() as session:
            row = session.execute(sa.select(self.table.c.id).where(self.table.c.id == key_id)).first()
            if row is None:
                return False
            session.execute(self.table.update().where(
                self.table.c.id == key_id, self.table.c.revokedAt.is_(None),
            ).values(revokedAt=time.time()))
        return True

    def identity(self, key_id, digest=None):
        with self.storage.sessions() as session:
            row = session.execute(sa.select(self.table).where(self.table.c.id == key_id)).mappings().first()
        if row is None or row["revokedAt"] is not None:
            return None
        if row["expiresAt"] is not None and row["expiresAt"] <= time.time():
            return None
        if digest is not None and not hmac.compare_digest(row["digest"], digest):
            return None
        service = row["service"]
        identity = hashlib.sha256(("component-services\0" + service).encode()).hexdigest() if service else row["id"]
        return Principal(identity, row["label"], frozenset(row["scopes"]),
                         kind="service" if service else "api-key", realm="component-services" if service else None,
                         subject=service, key_id=row["id"])

    def authenticate(self, authorization):
        prefix = "Bearer lrk_"
        if not authorization or not authorization.startswith(prefix) or len(authorization) > 256:
            return None
        token = authorization[len("Bearer "):]
        key_id, separator, _ = token[len("lrk_"):].partition(".")
        if not separator or len(key_id) != 32:
            return None
        return self.identity(key_id, hashlib.sha256(token.encode()).hexdigest())

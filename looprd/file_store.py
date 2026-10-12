"""Persistent, bounded, single-use file invitations owned by the operator."""

import hashlib
import re
import secrets
import time

from sqlalchemy import Column, Float, Integer, MetaData, String, Table, create_engine, delete, select, update

LIFETIME = 12 * 3600
TOKEN = re.compile(r"^[A-Za-z0-9_-]{43}$")


class FileError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


class FileStore:
    def __init__(self, settings):
        self.root = settings.data / "files"
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.limit, self.quota = settings.file_max_bytes, settings.file_quota_bytes
        self.engine = create_engine("sqlite:///" + str(self.root / "index.sqlite"),
                                    connect_args={"check_same_thread": False})
        metadata = MetaData()
        self.files = Table("files_v1", metadata,
            Column("id", String, primary_key=True), Column("upload_hash", String, unique=True),
            Column("name", String, nullable=False), Column("state", String, nullable=False),
            Column("size", Integer, nullable=False), Column("created", Float, nullable=False),
            Column("expires", Float, nullable=False))
        metadata.create_all(self.engine)
        with self.engine.begin() as db:
            db.execute(update(self.files).where(self.files.c.state == "uploading").values(state="failed"))
        self.clean()
        # A crash may leave uncommitted bytes. Never expose or retain those files.
        with self.engine.connect() as db:
            ready = set(db.execute(select(self.files.c.id).where(self.files.c.state == "ready")).scalars())
        for path in self.root.glob("*.blob"):
            if path.stem not in ready:
                path.unlink(missing_ok=True)

    def clean(self):
        now = time.time()
        with self.engine.begin() as db:
            expired = list(db.execute(select(self.files.c.id).where(self.files.c.expires <= now)).scalars())
            for file_id in expired:
                self.path(file_id).unlink(missing_ok=True)
            db.execute(delete(self.files).where(self.files.c.expires <= now))

    def path(self, file_id):
        if not TOKEN.fullmatch(file_id):
            raise FileError(404, "file not found")
        return self.root / (file_id + ".blob")

    def create(self, name):
        self.clean()
        file_id, token, now = secrets.token_urlsafe(32), secrets.token_urlsafe(32), time.time()
        row = dict(id=file_id, upload_hash=hashlib.sha256(token.encode()).hexdigest(), name=name,
                   state="pending", size=0, created=now, expires=now + LIFETIME)
        with self.engine.begin() as db:
            if len(db.execute(select(self.files.c.id)).all()) >= 128:
                raise FileError(409, "invitation limit reached; delete unused invitations")
            db.execute(self.files.insert().values(**row))
        return row, token

    def list(self):
        self.clean()
        with self.engine.connect() as db:
            return list(db.execute(select(self.files).order_by(self.files.c.created.desc())).mappings())

    def get(self, file_id):
        self.path(file_id)
        with self.engine.connect() as db:
            row = db.execute(select(self.files).where(self.files.c.id == file_id)).mappings().first()
        if row is None or row["expires"] <= time.time():
            raise FileError(404, "file not found")
        return row

    def claim(self, token):
        if not TOKEN.fullmatch(token):
            raise FileError(404, "upload invitation not found")
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.engine.begin() as db:
            row = db.execute(select(self.files).where(self.files.c.upload_hash == digest)).mappings().first()
            if row is None or row["expires"] <= time.time():
                raise FileError(404, "upload invitation not found")
            if row["state"] != "pending":
                raise FileError(409, "upload invitation already used; request a new one")
            rows = db.execute(select(self.files.c.state, self.files.c.size)).all()
            reserved = sum(self.limit if state == "uploading" else size for state, size in rows)
            if reserved + self.limit > self.quota:
                raise FileError(507, "file storage quota reached")
            db.execute(update(self.files).where(self.files.c.id == row["id"]).values(state="uploading"))
        return row

    def finish(self, file_id, size):
        row = self.get(file_id)
        if row["state"] != "uploading":
            raise FileError(409, "upload no longer active")
        with self.engine.begin() as db:
            db.execute(update(self.files).where(self.files.c.id == file_id).values(state="ready", size=size))
        return self.get(file_id)

    def fail(self, file_id):
        self.path(file_id).unlink(missing_ok=True)
        with self.engine.begin() as db:
            db.execute(update(self.files).where(self.files.c.id == file_id).values(state="failed", size=0))

    def remove(self, file_id):
        row = self.get(file_id)
        if row["state"] == "uploading":
            raise FileError(409, "upload in progress; retry deletion after completion")
        self.path(file_id).unlink(missing_ok=True)
        with self.engine.begin() as db:
            db.execute(delete(self.files).where(self.files.c.id == file_id))

    def close(self):
        self.engine.dispose()

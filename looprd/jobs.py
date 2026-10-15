"""Bounded SQL job queue, periodic schedules and compare-and-swap execution leases."""

import json
import secrets
import time

import sqlalchemy as sa

from .outbox import EventError


class Jobs:
    def __init__(self, storage):
        self.storage = storage
        metadata = sa.MetaData()
        self.guard = sa.Table("_looprd_job_guard_v1", metadata, sa.Column("id", sa.Integer, primary_key=True))
        self.schedule = sa.Table("_looprd_schedules_v1", metadata,
            sa.Column("name", sa.String(48), primary_key=True), sa.Column("next", sa.Float, nullable=False))
        self.table = sa.Table("_looprd_jobs_v1", metadata,
            sa.Column("id", sa.String(32), primary_key=True), sa.Column("name", sa.String(48), nullable=False),
            sa.Column("owner", sa.String(128), nullable=False), sa.Column("payload", sa.JSON),
            sa.Column("state", sa.String(20), nullable=False), sa.Column("next", sa.Float, nullable=False),
            sa.Column("attempts", sa.Integer, nullable=False), sa.Column("lease", sa.String(32)),
            sa.Column("until", sa.Float), sa.Column("finished", sa.Float), sa.Column("error", sa.String(80)))
        self.metadata = metadata

    def initialize(self, connection):
        self.metadata.create_all(connection)
        if connection.execute(sa.select(self.guard.c.id)).first() is None:
            connection.execute(self.guard.insert().values(id=1))
        for name, definition in self.storage.spec.jobs.items():
            if definition.intervalSeconds and connection.execute(sa.select(self.schedule.c.name).where(self.schedule.c.name == name)).first() is None:
                connection.execute(self.schedule.insert().values(name=name, next=time.time() + definition.intervalSeconds))

    def lock(self, session):
        session.execute(self.guard.update().where(self.guard.c.id == 1).values(id=1))

    def enqueue(self, name, payload, owner, delay=0, session=None):
        if name not in self.storage.spec.jobs:
            raise EventError(404, "job definition not found")
        if len(json.dumps(payload, allow_nan=False).encode()) > 16384:
            raise EventError(413, "job payload exceeds 16 KiB")
        if session is None:
            with self.storage.sessions.begin() as transaction:
                return self.enqueue(name, payload, owner, delay, transaction)
        if session.get_bind() is not self.storage.engine:
            raise ValueError("job transaction belongs to another component")
        self.lock(session)
        count = session.execute(sa.select(sa.func.count()).select_from(self.table)
                                .where(self.table.c.state.in_(["pending", "running"]))).scalar_one()
        if count >= 1000:
            raise EventError(503, "job queue capacity reached")
        identity = secrets.token_hex(16)
        session.execute(self.table.insert().values(id=identity, name=name, owner=owner, payload=payload,
            state="pending", next=time.time() + delay, attempts=0, lease=None, until=None, finished=None, error=None))
        return identity

    def claim(self, worker, timeout):
        now = time.time()
        with self.storage.sessions.begin() as session:
            self.lock(session)
            session.execute(self.table.delete().where(self.table.c.finished < now - 86400))
            completed = session.execute(sa.select(self.table.c.id).where(self.table.c.finished.is_not(None))
                                        .order_by(self.table.c.finished.desc()).offset(10000)).scalars().all()
            if completed:
                session.execute(self.table.delete().where(self.table.c.id.in_(completed)))
            for name, definition in self.storage.spec.jobs.items():
                if definition.intervalSeconds:
                    due = session.execute(sa.select(self.schedule.c.next).where(self.schedule.c.name == name)).scalar()
                    if due is not None and due <= now:
                        try:
                            self.enqueue(name, None, "system", session=session)
                        except EventError as error:
                            if error.status != 503:
                                raise
                            continue
                        session.execute(self.schedule.update().where(self.schedule.c.name == name).values(next=now + definition.intervalSeconds))
            candidates = session.execute(sa.select(self.table).where(self.table.c.next <= now,
                sa.or_(self.table.c.state == "pending", sa.and_(self.table.c.state == "running", self.table.c.until < now)))
                .order_by(self.table.c.next, self.table.c.id).limit(16)).mappings().all()
            for job in candidates:
                definition = self.storage.spec.jobs.get(job["name"])
                if not definition or job["attempts"] >= definition.maxAttempts:
                    session.execute(self.table.update().where(self.table.c.id == job["id"])
                        .values(state="failed", finished=now, error="DefinitionRemovedOrRetriesExhausted"))
                    continue
                updated = session.execute(self.table.update().where(self.table.c.id == job["id"],
                    sa.or_(self.table.c.state == "pending", self.table.c.until < now)).values(
                    state="running", attempts=self.table.c.attempts + 1, lease=worker, until=now + timeout + 30).returning(self.table)).mappings().first()
                if updated:
                    return dict(updated)
        return None

    def finish(self, job, worker, error=None):
        definition = self.storage.spec.jobs[job["name"]]
        retry = error is not None and job["attempts"] < definition.maxAttempts
        with self.storage.sessions.begin() as session:
            session.execute(self.table.update().where(self.table.c.id == job["id"], self.table.c.lease == worker,
                self.table.c.attempts == job["attempts"], self.table.c.state == "running").values(
                state="pending" if retry else "failed" if error else "complete", lease=None, until=None,
                next=time.time() + definition.retrySeconds, error=error,
                finished=None if retry else time.time(), payload=job["payload"] if error else None))

    @staticmethod
    def describe(row):
        return {"id": row["id"], "name": row["name"], "state": row["state"], "attempts": row["attempts"],
                "nextRunAt": row["next"] * 1000, "finishedAt": row["finished"] * 1000 if row["finished"] else None,
                "error": row["error"]}

    def list(self):
        with self.storage.sessions() as session:
            return [self.describe(row) for row in session.execute(sa.select(self.table).order_by(self.table.c.next.desc()).limit(100)).mappings()]

    def get(self, name, identity, owner):
        with self.storage.sessions() as session:
            row = session.execute(sa.select(self.table).where(self.table.c.name == name,
                self.table.c.id == identity, self.table.c.owner == owner)).mappings().first()
            if row is None:
                raise EventError(404, "job not found")
            return self.describe(row)

    def retry(self, identity):
        with self.storage.sessions.begin() as session:
            changed = session.execute(self.table.update().where(self.table.c.id == identity, self.table.c.state == "failed")
                .values(state="pending", next=time.time(), attempts=0, lease=None, until=None, finished=None, error=None))
            if not changed.rowcount:
                raise EventError(409, "job is not in a retryable failed state")

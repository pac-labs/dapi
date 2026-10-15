"""Commit-ordered SQL event log and identity-bound replay cursors."""

import base64
import hashlib
import hmac
import json
import secrets
import time
from contextlib import contextmanager

import sqlalchemy as sa


class EventError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


class Outbox:
    def __init__(self, storage):
        self.storage = storage
        self.spec = storage.spec
        metadata = sa.MetaData()
        self.clock = sa.Table("_looprd_event_clock_v1", metadata,
            sa.Column("id", sa.Integer, primary_key=True), sa.Column("sequence", sa.BigInteger, nullable=False),
            sa.Column("floor", sa.BigInteger, nullable=False), sa.Column("count", sa.Integer, nullable=False),
            sa.Column("secret", sa.String(64), nullable=False))
        self.events = sa.Table("_looprd_events_v1", metadata,
            sa.Column("sequence", sa.BigInteger, primary_key=True, autoincrement=False),
            sa.Column("resource", sa.String(48), nullable=False, index=True),
            sa.Column("created", sa.Float, nullable=False), sa.Column("sent", sa.Boolean, nullable=False),
            sa.Column("payload", sa.JSON, nullable=False), sa.Column("record", sa.JSON))
        self.metadata = metadata

    def initialize(self, connection):
        self.metadata.create_all(connection)
        if connection.execute(sa.select(self.clock.c.id)).first() is None:
            connection.execute(self.clock.insert().values(id=1, sequence=0, floor=0, count=0, secret=secrets.token_hex(32)))

    def locked(self, session):
        # The clock lock orders commit-visible events on SQLite and PostgreSQL alike.
        return session.execute(self.clock.update().where(self.clock.c.id == 1)
                               .values(sequence=self.clock.c.sequence).returning(self.clock)).mappings().one()

    def prune(self, session, clock):
        cutoff = time.time() - self.spec.realtime.retentionSeconds
        first_live = session.execute(sa.select(sa.func.min(self.events.c.sequence))
                                     .where(self.events.c.created >= cutoff)).scalar()
        floor = (first_live - 1) if first_live is not None else clock["sequence"]
        floor = max(clock["floor"], floor)
        removed = session.execute(self.events.delete().where(self.events.c.sequence <= floor)).rowcount
        if floor != clock["floor"] or removed:
            session.execute(self.clock.update().values(floor=floor, count=self.clock.c.count - removed))
        return floor, clock["count"] - removed

    def begin(self, session):
        if session.get_bind() is not self.storage.engine:
            raise ValueError("event transaction belongs to a different component")
        clock = self.locked(session)
        _, count = self.prune(session, clock)
        if count >= self.spec.realtime.maxEvents:
            raise EventError(503, "event capacity reached; retained history must expire before retry")
        session.info["looprd_event_transaction"] = (self.spec.component, session.get_transaction(), count)

    @contextmanager
    def transaction(self):
        with self.storage.sessions.begin() as session:
            self.begin(session)
            yield session

    def emit(self, session, event, record=None):
        reserved = session.info.get("looprd_event_transaction")
        if reserved is None or reserved[0] != self.spec.component or reserved[1] is not session.get_transaction():
            raise ValueError("use ctx.transaction() before mutating data and emitting an event")
        if reserved[2] >= self.spec.realtime.maxEvents:
            raise EventError(503, "event capacity reached")
        if not isinstance(event, dict) or event.get("resource") not in self.spec.websocket.resources:
            raise ValueError("event requires a declared WebSocket resource")
        if "eventId" in event or "cursor" in event:
            raise ValueError("event IDs and cursors are runtime-owned")
        event = {**event, "component": self.spec.component, "eventId": secrets.token_hex(16)}
        raw = json.dumps(event, allow_nan=False, default=lambda value: value.isoformat())
        if len(raw.encode()) > self.spec.realtime.maxEventBytes:
            raise EventError(413, "durable event exceeds maximum size")
        event = json.loads(raw)
        sequence = session.execute(self.clock.update().values(sequence=self.clock.c.sequence + 1)
                                   .returning(self.clock.c.sequence)).scalar_one()
        session.execute(self.events.insert().values(sequence=sequence, resource=event["resource"],
                        created=time.time(), sent=False, payload=event, record=record if record is not None else event.get("data")))
        session.execute(self.clock.update().values(count=self.clock.c.count + 1))
        session.info["looprd_event_transaction"] = (reserved[0], reserved[1], reserved[2] + 1)
        return event

    def cursor(self, resource, principal, sequence, secret):
        value = f"{self.spec.component}\0{resource}\0{principal.id}\0{sequence}".encode()
        signature = hmac.new(secret.encode(), value, hashlib.sha256).hexdigest()
        return base64.urlsafe_b64encode(f"{sequence}.{signature}".encode()).decode().rstrip("=")

    def position(self, resource, principal, cursor, clock, live=False):
        if cursor is None:
            return clock["sequence"] if live else clock["floor"]
        try:
            if len(cursor) > 256:
                raise ValueError()
            sequence = int(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode().split(".")[0])
            expected = self.cursor(resource, principal, sequence, clock["secret"])
            if sequence < 0 or sequence > clock["sequence"] or not hmac.compare_digest(cursor, expected):
                raise ValueError()
        except (ValueError, UnicodeError, TypeError):
            raise EventError(400, "invalid event cursor") from None
        if sequence < clock["floor"]:
            raise EventError(410, "event history expired; fetch current resource state and reset")
        return sequence

    def page(self, resource, principal, cursor=None, limit=100, live=False):
        with self.storage.sessions.begin() as session:
            clock = self.locked(session)
            floor, _ = self.prune(session, clock)
            clock = {**clock, "floor": floor}
            sequence = self.position(resource, principal, cursor, clock, live)
            result = session.execute(sa.select(self.events).where(self.events.c.resource == resource,
                self.events.c.sequence > sequence, self.events.c.sequence <= clock["sequence"])
                .order_by(self.events.c.sequence).limit(1001)).yield_per(1).mappings()
            rows, size, more = [], 0, False
            position = sequence
            try:
                for scanned, row in enumerate(result):
                    if scanned >= 1000:
                        more = True
                        break
                    if not self.storage.policies.can_event(session, resource, principal, row["record"]):
                        position = row["sequence"]
                        continue
                    cost = len(json.dumps(row["payload"]).encode()) + 256
                    if rows and (len(rows) == limit or size + cost > self.spec.realtime.maxReplayBytes):
                        more = True
                        break
                    if cost > self.spec.realtime.maxReplayBytes:
                        raise EventError(413, "retained event exceeds replay byte budget")
                    size += cost
                    rows.append(row)
                    position = row["sequence"]
            finally:
                result.close()
            events = [{**row["payload"], "cursor": self.cursor(resource, principal, row["sequence"], clock["secret"])}
                      for row in rows]
            position = position if more else clock["sequence"]
            return {"events": events, "cursor": self.cursor(resource, principal, position, clock["secret"]),
                    "startCursor": self.cursor(resource, principal, sequence, clock["secret"]),
                    "hasMore": more}

    def pending(self):
        with self.storage.sessions.begin() as session:
            self.prune(session, self.locked(session))
            return [dict(row) for row in session.execute(sa.select(self.events).where(self.events.c.sent.is_(False))
                    .order_by(self.events.c.sequence).limit(100)).mappings()]

    def sent(self, sequence):
        with self.storage.sessions.begin() as session:
            session.execute(self.events.update().where(self.events.c.sequence == sequence).values(sent=True))

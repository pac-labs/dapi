"""Component-owned device capabilities and transaction-bound, coalesced wake jobs."""

import hashlib
import hmac
import secrets
import time

import sqlalchemy as sa

from .outbox import EventError
from .push_transport import topic


class PushStore:
    def __init__(self, storage):
        self.storage, self.spec = storage, storage.spec
        metadata = sa.MetaData()
        self.devices = sa.Table("_looprd_push_devices_v1", metadata,
            sa.Column("id", sa.String(32), primary_key=True),
            sa.Column("owner", sa.String(128), nullable=False),
            sa.Column("keyId", sa.String(32)),
            sa.Column("installation", sa.String(128), nullable=False),
            sa.Column("endpoint", sa.String(2048), nullable=False),
            sa.Column("endpointHash", sa.String(64), nullable=False, unique=True),
            sa.Column("expires", sa.Float, nullable=False),
            sa.Column("verified", sa.Boolean, nullable=False),
            sa.Column("challengeHash", sa.String(64)),
            sa.Column("challengeExpires", sa.Float, nullable=False),
            sa.UniqueConstraint("owner", "installation"))
        self.queue = sa.Table("_looprd_push_queue_v1", metadata,
            sa.Column("device", sa.String(32), sa.ForeignKey(self.devices.c.id, ondelete="CASCADE"), primary_key=True),
            sa.Column("payload", sa.JSON, nullable=False),
            sa.Column("version", sa.String(32), nullable=False),
            sa.Column("attempts", sa.Integer, nullable=False),
            sa.Column("due", sa.Float, nullable=False),
            sa.Column("lease", sa.Float, nullable=False),
            sa.Column("expires", sa.Float, nullable=False),
            sa.Column("error", sa.String(32)))
        self.metadata = metadata

    def initialize(self, connection):
        self.metadata.create_all(connection)

    def lock(self, session):
        self.storage.events.locked(session)
        now = time.time()
        session.execute(self.devices.delete().where(sa.or_(self.devices.c.expires <= now,
            sa.and_(self.devices.c.verified.is_(False), self.devices.c.challengeExpires <= now))))
        session.execute(self.queue.delete().where(self.queue.c.expires <= now))

    @staticmethod
    def metadata_row(row):
        return {"id": row["id"], "installationId": row["installation"], "expiresAt": row["expires"],
                "status": "active" if row["verified"] else "pending",
                "verificationExpiresAt": row["challengeExpires"] if not row["verified"] else None}

    def list(self, principal):
        with self.storage.sessions.begin() as session:
            self.lock(session)
            return [self.metadata_row(row) for row in session.execute(sa.select(self.devices)
                .where(self.devices.c.owner == principal.id).order_by(self.devices.c.id)).mappings()]

    def register(self, principal, installation, endpoint):
        topic(self.storage.settings, endpoint)
        now = time.time()
        expires = min(now + self.spec.push.deviceLifetimeSeconds, principal.expires_at or float("inf"))
        if expires <= now:
            raise EventError(401, "identity expired")
        digest = hashlib.sha256(endpoint.encode()).hexdigest()
        with self.storage.sessions.begin() as session:
            self.lock(session)
            previous = session.execute(sa.select(self.devices).where(self.devices.c.owner == principal.id,
                self.devices.c.installation == installation)).mappings().first()
            identity = previous["id"] if previous else secrets.token_hex(16)
            occupied = session.execute(sa.select(self.devices.c.id).where(self.devices.c.endpointHash == digest)).scalar()
            if occupied and occupied != identity:
                raise EventError(409, "endpoint already registered")
            if previous is None:
                count = session.execute(sa.select(sa.func.count()).select_from(self.devices)).scalar()
                owned = session.execute(sa.select(sa.func.count()).select_from(self.devices)
                    .where(self.devices.c.owner == principal.id)).scalar()
                if count >= self.spec.push.maxDevices or owned >= self.spec.push.maxDevicesPerPrincipal:
                    raise EventError(429, "push device capacity reached")
            renew = bool(previous and previous["verified"] and previous["endpointHash"] == digest)
            challenge = secrets.token_urlsafe(32)
            row = dict(id=identity, owner=principal.id, keyId=principal.key_id, installation=installation,
                endpoint=endpoint, endpointHash=digest, expires=expires, verified=renew,
                challengeHash=None if renew else hashlib.sha256(challenge.encode()).hexdigest(), challengeExpires=now + 600)
            if previous:
                session.execute(self.devices.update().where(self.devices.c.id == identity).values(**row))
            else:
                session.execute(self.devices.insert().values(**row))
            if not renew:
                session.execute(self.queue.delete().where(self.queue.c.device == identity))
                self.enqueue(session, identity, {"apiVersion": "looprd-push/v1", "type": "verify",
                    "deviceId": identity, "challenge": challenge}, now + 600)
            else:
                session.execute(self.queue.update().where(self.queue.c.device == identity).values(version=secrets.token_hex(16)))
            return self.metadata_row(row)

    def verify(self, principal, identity, challenge):
        with self.storage.sessions.begin() as session:
            self.lock(session)
            row = session.execute(sa.select(self.devices).where(self.devices.c.id == identity,
                self.devices.c.owner == principal.id)).mappings().first()
            if not row:
                raise EventError(404, "device not found")
            if not row["challengeHash"] or not hmac.compare_digest(row["challengeHash"], hashlib.sha256(challenge.encode()).hexdigest()):
                raise EventError(403, "invalid device challenge")
            session.execute(self.devices.update().where(self.devices.c.id == identity).values(verified=True, challengeHash=None))
            session.execute(self.queue.delete().where(self.queue.c.device == identity))
            return self.metadata_row({**row, "verified": True})

    def remove(self, principal, identity):
        with self.storage.sessions.begin() as session:
            self.lock(session)
            return bool(session.execute(self.devices.delete().where(self.devices.c.id == identity,
                self.devices.c.owner == principal.id)).rowcount)

    def require_transaction(self, session):
        reserved = session.info.get("looprd_event_transaction")
        if (session.get_bind() is not self.storage.engine or not reserved
                or reserved[0] != self.spec.component or reserved[1] is not session.get_transaction()):
            raise ValueError("push requires the component event transaction")

    def enqueue(self, session, identity, payload, expires):
        previous = session.execute(sa.select(self.queue).where(self.queue.c.device == identity)).mappings().first()
        values = dict(payload=payload, version=secrets.token_hex(16),
                      attempts=0, due=time.time(), expires=expires, error=None)
        if previous:
            session.execute(self.queue.update().where(self.queue.c.device == identity).values(**values))
        else:
            session.execute(self.queue.insert().values(device=identity, lease=0, **values))

    def notify(self, session, principal_id):
        """Trusted server recipient selection; no arbitrary message bodies accepted."""
        self.require_transaction(session)
        now = time.time()
        devices = session.execute(sa.select(self.devices.c.id).where(self.devices.c.owner == principal_id,
            self.devices.c.verified.is_(True), self.devices.c.expires > now)).scalars().all()
        for identity in devices:
            self.enqueue(session, identity, {"apiVersion": "looprd-push/v1", "type": "sync"}, now + self.spec.push.retrySeconds)
        return len(devices)

    def revoke(self, session, principal_id):
        self.require_transaction(session)
        return session.execute(self.devices.delete().where(self.devices.c.owner == principal_id)).rowcount

    def claim(self):
        now = time.time()
        with self.storage.sessions.begin() as session:
            self.lock(session)
            row = session.execute(sa.select(self.queue, self.devices.c.endpoint, self.devices.c.keyId)
                .join(self.devices).where(self.queue.c.due <= now, self.queue.c.lease <= now,
                    self.queue.c.attempts < 8).order_by(self.queue.c.due).limit(1)).mappings().first()
            if not row:
                return None
            session.execute(self.queue.update().where(self.queue.c.device == row["device"]).values(lease=now + 30))
            return dict(row)

    def finish(self, job, status):
        with self.storage.sessions.begin() as session:
            self.lock(session)
            current = session.execute(sa.select(self.queue.c.version).where(self.queue.c.device == job["device"])).scalar()
            if current != job["version"]:
                session.execute(self.queue.update().where(self.queue.c.device == job["device"]).values(lease=0))
                return
            if status in (404, 410):
                # A changed registration must not be removed by an older request.
                session.execute(self.devices.delete().where(self.devices.c.id == job["device"],
                    self.devices.c.endpoint == job["endpoint"]))
                return
            condition = sa.and_(self.queue.c.device == job["device"], self.queue.c.version == job["version"])
            if 200 <= status < 300:
                session.execute(self.queue.delete().where(condition))
            else:
                attempts = job["attempts"] + 1
                permanent = 400 <= status < 500 and status != 429
                session.execute(self.queue.update().where(condition).values(lease=0,
                    attempts=8 if permanent else attempts, error="http_" + str(status) if status else "network_error",
                    due=time.time() + min(3600, 5 * 2 ** attempts)))
            session.execute(self.queue.update().where(self.queue.c.device == job["device"],
                self.queue.c.version != job["version"]).values(lease=0))

    def status(self):
        with self.storage.sessions.begin() as session:
            self.lock(session)
            return {"devices": session.execute(sa.select(sa.func.count()).select_from(self.devices)).scalar(),
                "queued": session.execute(sa.select(sa.func.count()).select_from(self.queue).where(self.queue.c.attempts < 8)).scalar(),
                "failed": session.execute(sa.select(sa.func.count()).select_from(self.queue).where(self.queue.c.attempts >= 8)).scalar()}

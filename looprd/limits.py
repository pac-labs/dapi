"""Bounded local or Redis counters and SQL-backed native row quotas."""

import hashlib
import time
from urllib.parse import urlsplit

import redis.asyncio as redis
from redis.exceptions import RedisError
import sqlalchemy as sa

from .outbox import EventError


class RateLimiter:
    def __init__(self, spec, settings):
        self.spec, self.settings = spec, settings
        self.values = {}
        self.error = None
        self.client = None
        if spec.limits.backend == "redis":
            url = settings.secret(spec.limits.connectionSecret)
            parsed = urlsplit(url)
            if parsed.scheme not in ("redis", "rediss") or not parsed.hostname or parsed.query or parsed.fragment:
                raise ValueError("invalid Redis limits URL")
            self.client = redis.Redis.from_url(url, socket_timeout=3, socket_connect_timeout=3, max_connections=4, protocol=2)

    async def increment(self, identity, period, limit):
        key = hashlib.sha256(identity.encode()).hexdigest() + ":" + str(period)
        if self.client:
            key = self.settings.event_namespace + ":limits:" + self.spec.component + ":" + key
            try:
                async with self.client.pipeline(transaction=True) as pipeline:
                    # redis-py's incr helper emits INCRBY; keep the documented narrow INCR ACL.
                    pipeline.execute_command("INCR", key)
                    pipeline.expire(key, period, nx=True)
                    count = (await pipeline.execute())[0]
                self.error = None
            except (RedisError, OSError) as error:
                self.error = type(error).__name__
                raise EventError(503, "rate-limit service unavailable") from None
        else:
            now = time.monotonic()
            self.values = {name: value for name, value in self.values.items() if value[1] > now}
            if key not in self.values and len(self.values) >= 4096:
                raise EventError(429, "rate-limit identity capacity reached")
            count, expiry = self.values.get(key, (0, now + period))
            count += 1
            self.values[key] = (count, expiry)
        if count > limit:
            raise EventError(429, "request rate limit exceeded")

    async def check(self, identity, authentication=False):
        limits = self.spec.limits
        if authentication:
            if limits.authAttemptsPerMinute:
                await self.increment("auth:" + identity, 60, limits.authAttemptsPerMinute)
            return
        if limits.requestsPerMinute:
            await self.increment("request:" + identity, 60, limits.requestsPerMinute)
        if limits.burstPerSecond:
            await self.increment("burst:" + identity, 1, limits.burstPerSecond)

    async def close(self):
        if self.client:
            await self.client.aclose()


class RowQuota:
    def __init__(self, storage):
        self.storage = storage
        self.table = sa.Table("_looprd_quota_v1", sa.MetaData(), sa.Column("id", sa.Integer, primary_key=True))

    def initialize(self, connection):
        self.table.create(connection, checkfirst=True)
        if connection.execute(sa.select(self.table.c.id)).first() is None:
            connection.execute(self.table.insert().values(id=1))

    def lock(self, session):
        session.execute(self.table.update().where(self.table.c.id == 1).values(id=1))

    def check(self, session, table):
        count = session.execute(sa.select(sa.func.count()).select_from(table)).scalar_one()
        if count >= self.storage.spec.limits.maxRows:
            raise EventError(507, "native resource row quota reached")

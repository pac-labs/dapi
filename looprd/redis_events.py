"""Best-effort Redis bridge: bounded, asynchronous and independent of SQL commits."""

import asyncio
import json
import secrets
from urllib.parse import urlsplit

import redis.asyncio as redis
from redis.exceptions import RedisError

MAX_MESSAGE = 1_048_576


class RedisBridge:
    def __init__(self, url, channel, hub, local):
        parsed = urlsplit(url)
        if parsed.scheme not in ("redis", "rediss") or not parsed.hostname or parsed.query or parsed.fragment:
            raise ValueError("Redis secret must be a redis:// or rediss:// URL without query or fragment")
        self.channel, self.hub, self.local = channel, hub, local
        self.queue = asyncio.Queue(local.queue_size)
        self.receiving = False
        self.error = None
        self.publish_error = None
        self.dropped = 0
        self.client = redis.Redis.from_url(url, socket_connect_timeout=3, socket_timeout=3,
                                          max_connections=4, protocol=2, health_check_interval=5)
        self.tasks = [asyncio.create_task(self.receive()), asyncio.create_task(self.send())]

    def status(self):
        return {"backend": "redis", "delivery": "best-effort",
                "state": "connected" if self.receiving and not self.publish_error else "degraded",
                "error": self.publish_error or self.error, "droppedRemoteEvents": self.dropped,
                "pendingRemoteEvents": self.queue.qsize()}

    def publish(self, event):
        if not self.receiving:
            self.dropped += 1
            return
        try:
            message = json.dumps({"version": 1, "origin": self.hub.origin,
                                  "id": event.get("eventId", secrets.token_hex(16)), "event": event}, allow_nan=False).encode()
            if len(message) > MAX_MESSAGE:
                raise ValueError("event too large")
            self.queue.put_nowait(message)
        except (TypeError, ValueError, RecursionError, asyncio.QueueFull):
            self.dropped += 1

    async def publish_confirmed(self, event):
        message = json.dumps({"version": 1, "origin": self.hub.origin,
                              "id": event["eventId"], "event": event}, allow_nan=False).encode()
        if len(message) > MAX_MESSAGE:
            raise ValueError("event envelope exceeds transport limit")
        try:
            await self.client.publish(self.channel, message)
            self.publish_error = None
        except (RedisError, OSError) as error:
            self.publish_error = type(error).__name__
            raise

    async def send(self):
        while True:
            message = await self.queue.get()
            try:
                await self.client.publish(self.channel, message)
                self.publish_error = None
            except (RedisError, OSError) as error:
                self.publish_error = type(error).__name__
                self.dropped += 1

    def deliver(self, data):
        try:
            if not isinstance(data, bytes) or len(data) > MAX_MESSAGE:
                raise ValueError("invalid message size")
            envelope = json.loads(data)
            if not isinstance(envelope, dict) or envelope.get("version") != 1:
                raise ValueError("unknown envelope")
            if envelope.get("origin") == self.hub.origin:
                return
            event, event_id = envelope["event"], envelope["id"]
            if (not isinstance(event, dict) or not isinstance(event.get("resource"), str)
                    or not isinstance(event_id, str) or len(event_id) != 32):
                raise ValueError("invalid event")
            if self.hub.remember((self.channel, event_id)):
                self.local.publish(event)
        except (ValueError, KeyError, TypeError, UnicodeError, RecursionError):
            self.dropped += 1

    async def receive(self):
        delay = 0.25
        while True:
            try:
                async with self.client.pubsub() as subscription:
                    await subscription.subscribe(self.channel)
                    while True:
                        message = await subscription.get_message(ignore_subscribe_messages=False, timeout=1)
                        if message and message["type"] == "subscribe":
                            self.receiving, self.error, delay = True, None, 0.25
                        elif message and message["type"] == "message":
                            self.deliver(message["data"])
            except (RedisError, OSError) as error:
                self.receiving = False
                self.error = type(error).__name__
                await asyncio.sleep(delay)
                delay = min(delay * 2, 5)

    async def close(self):
        self.receiving = False
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.client.aclose()

"""Deterministic Redis transport substitute, including disconnects and echo."""

import asyncio
from collections import defaultdict

from redis.exceptions import ConnectionError


class Broker:
    def __init__(self):
        self.channels = defaultdict(set)
        self.up = True
        self.sent = []

    def client(self, *args, **kwargs):
        return Client(self)


class Client:
    def __init__(self, broker):
        self.broker = broker

    def pubsub(self):
        return Subscription(self.broker)

    async def publish(self, channel, data):
        if not self.broker.up:
            raise ConnectionError("credentials must never appear in status")
        self.broker.sent.append((channel, data))
        for queue in self.broker.channels[channel]:
            queue.put_nowait({"type": "message", "data": data})

    async def aclose(self):
        pass


class Subscription:
    def __init__(self, broker):
        self.broker, self.queue, self.channel = broker, asyncio.Queue(), None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        if self.channel:
            self.broker.channels[self.channel].discard(self.queue)

    async def subscribe(self, channel):
        if not self.broker.up:
            raise ConnectionError("test outage")
        self.channel = channel
        self.broker.channels[channel].add(self.queue)
        self.queue.put_nowait({"type": "subscribe"})

    async def get_message(self, **kwargs):
        if not self.broker.up:
            raise ConnectionError("test outage")
        try:
            return await asyncio.wait_for(self.queue.get(), 0.01)
        except TimeoutError:
            return None

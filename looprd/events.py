"""Bounded event subscriptions and one-use browser WebSocket tickets."""

import asyncio
import secrets
import time
from dataclasses import dataclass, field


@dataclass(eq=False)
class Subscriber:
    queue: asyncio.Queue
    resources: set[str] = field(default_factory=set)
    overflow: bool = False
    cursors: dict[str, str] = field(default_factory=dict)


class EventBus:
    def __init__(self, queue_size):
        self.queue_size = queue_size
        self.subscribers: set[Subscriber] = set()

    def subscribe(self):
        subscriber = Subscriber(asyncio.Queue(self.queue_size))
        self.subscribers.add(subscriber)
        return subscriber

    def publish(self, event):
        for subscriber in tuple(self.subscribers):
            if event["resource"] not in subscriber.resources or subscriber.overflow:
                continue
            try:
                subscriber.queue.put_nowait(event)
            except asyncio.QueueFull:
                subscriber.overflow = True
                # Wake a consumer even if it has not yet drained its queue.
                subscriber.queue.get_nowait()
                subscriber.queue.put_nowait(None)


class Tickets:
    def __init__(self):
        self.values = {}

    def issue(self, generation, principal=None):
        now = time.monotonic()
        self.values = {k: v for k, v in self.values.items() if v[1] > now}
        if len(self.values) >= 4096:
            raise ValueError("too many outstanding tickets")
        ticket = secrets.token_urlsafe(32)
        self.values[ticket] = (generation, now + 30, principal)
        return ticket

    def consume(self, ticket, generation):
        value = self.values.pop(ticket, None)
        return value if value and value[0] == generation and value[1] > time.monotonic() else None

    def revoke(self, generation):
        self.values = {key: value for key, value in self.values.items() if value[0] != generation}

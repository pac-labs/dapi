"""Generation ownership of shared local buses and optional remote bridges."""

import hashlib
import asyncio
import secrets
from collections import OrderedDict

from .events import EventBus
from .redis_events import RedisBridge


class GenerationBus:
    def __init__(self, local, bridge=None, key=None):
        self.local, self.bridge, self.key = local, bridge, key

    @property
    def subscribers(self):
        return self.local.subscribers

    def subscribe(self):
        return self.local.subscribe()

    def publish(self, event):
        self.local.publish(event)
        if self.bridge:
            self.bridge.publish(event)

    def status(self):
        return self.bridge.status() if self.bridge else {"backend": "memory", "delivery": "best-effort", "state": "local"}


class EventHub:
    def __init__(self, settings, buses):
        self.settings, self.buses = settings, buses
        self.origin = secrets.token_hex(16)
        self.bridges, self.references = {}, {}
        self.seen = OrderedDict()

    def remember(self, key):
        if key in self.seen:
            return False
        self.seen[key] = None
        if len(self.seen) > 4096:
            self.seen.popitem(last=False)
        return True

    def acquire(self, spec):
        local = self.buses.setdefault(spec.component, EventBus(self.settings.event_queue))
        if spec.realtime.backend == "memory":
            return GenerationBus(local)
        url = self.settings.secret(spec.realtime.connectionSecret)
        channel = f"{self.settings.event_namespace}:{spec.component}:{spec.api.version}:events"
        key = (channel, hashlib.sha256(url.encode()).hexdigest())
        if key not in self.bridges:
            self.bridges[key] = RedisBridge(url, channel, self, local)
            self.references[key] = 0
        self.references[key] += 1
        return GenerationBus(local, self.bridges[key], key)

    async def release(self, bus):
        if bus.key is not None:
            self.references[bus.key] -= 1
            if self.references[bus.key] == 0:
                bridge = self.bridges.pop(bus.key)
                self.references.pop(bus.key)
                closing = asyncio.create_task(bridge.close())
                try:
                    await asyncio.shield(closing)
                except asyncio.CancelledError:
                    await closing
                    raise

    async def close(self):
        for bridge in self.bridges.values():
            await bridge.close()
        self.bridges.clear()
        self.references.clear()

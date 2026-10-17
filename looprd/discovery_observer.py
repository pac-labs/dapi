"""Coalesced sidecar reads after checkpoint publication, independently of activation."""

import asyncio
import logging

from .discovery_source import observe
from .discovery_store import DiscoveryStore

log = logging.getLogger("looprd")


class DiscoveryObserver:
    def __init__(self, runtime):
        self.runtime = runtime
        self.store = DiscoveryStore(runtime.settings.data)
        self.pending = None
        self.task = None
        self.sequence = 0

    def schedule(self, loaded):
        self.sequence += 1
        selected = self.runtime.settings.discovery_components
        active = {key: (value.digest, self.runtime.revisions[key]) for key, value in self.runtime.active.items()
                  if selected is None or key in selected}
        snapshots = {key: value for key, value in getattr(self.runtime.reader, "snapshots", {}).items() if key in loaded and key in active}
        self.pending = (self.sequence, active, snapshots)
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self.run())

    async def write(self, component, result, snapshot):
        task = asyncio.create_task(asyncio.to_thread(self.store.record, component, result, snapshot))
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise

    async def run(self):
        while self.pending:
            sequence, active, snapshots = self.pending
            self.pending = None
            try:
                known = {row["component"] for row in await asyncio.to_thread(self.store.list)}
            except Exception as error:
                self.runtime.record_failure(None, "discovery state", error)
                return
            newcomers = sorted(set(active) - known)
            room = max(0, 1000 - len(known))
            if len(newcomers) > room:
                self.runtime.record_failure(None, "discovery state", ValueError("discovery observation limit reached"))
            for component in sorted(known | set(newcomers[:room])):
                if sequence != self.sequence:
                    break
                snapshot = snapshots.get(component)
                try:
                    if component not in active:
                        result = {"state": "inactive", "reason": "component_not_active"}
                    elif component in self.runtime.availability.blocked:
                        result = {"state": "inactive", "reason": "component_blocked"}
                    elif snapshot is None:
                        result = {"state": "unavailable", "reason": "root_source_unavailable"}
                    else:
                        result = await observe(self.runtime.reader, snapshot, component, self.runtime.settings)
                    async with self.runtime.lock:
                        if sequence != self.sequence:
                            break
                        current = self.runtime.active.get(component)
                        current_version = (current.digest, self.runtime.revisions[component]) if current else None
                        if current_version != active.get(component):
                            continue
                        await self.write(component, result, snapshot)
                    if result["state"] in ("invalid", "unavailable"):
                        self.runtime.record_failure(component, "discovery observation", ValueError("sidecar observation " + result["state"]))
                except Exception as error:
                    self.runtime.record_failure(component, "discovery observation", error)
                    log.warning("component=%s stage=discovery error=%s", component, type(error).__name__)

    async def close(self):
        self.pending = None
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)

"""Reconcile authoritative sources and atomically persist successful generations."""

import asyncio
import json
import logging
import os

from starlette.concurrency import run_in_threadpool

from .component import Generation
from .events import Tickets
from .event_hub import EventHub
from .sources import SourceReader
from .spec import Specification
from .status import Monitor, snapshot
from .availability import Availability

log = logging.getLogger("looprd")


class Runtime:
    def __init__(self, settings, reader=None):
        self.settings = settings
        self.reader = reader or SourceReader(settings.sources)
        self.active = {}
        self.revisions = {}
        self.errors = {}
        self.sources_error = None
        self.sources = {}
        self.monitor = Monitor()
        self.buses = {}
        self.event_hub = EventHub(settings, self.buses)
        self.tickets = Tickets()
        self.lock = asyncio.Lock()
        self.retiring = set()
        self.poll_task = None
        settings.data.mkdir(parents=True, exist_ok=True)
        self.availability = Availability(settings.data)
        self.sockets = {}
        self.checkpoint = settings.data / "active.json"

    async def set_blocked(self, component, blocked):
        async with self.lock:
            known = set(self.active) | set(self.sources) | set(self.errors) | set(self.availability.blocked)
            if component not in known:
                raise KeyError(component)
            desired = set(self.availability.blocked)
            desired.add(component) if blocked else desired.discard(component)
            write = asyncio.create_task(asyncio.to_thread(self.availability.save, desired))
            try:
                await asyncio.shield(write)
            except asyncio.CancelledError:
                # A cancelled request cannot leave durable state ahead of the live gate.
                await write
                self.availability.blocked = frozenset(desired)
                raise
            self.availability.blocked = frozenset(desired)
            if blocked:
                generation = self.active.get(component)
                if generation:
                    self.tickets.revoke(generation.key)
                for socket, owner in tuple(self.sockets.items()):
                    if owner == component:
                        try:
                            await socket.close(code=1008)
                        except (RuntimeError, OSError):
                            pass

    async def restore(self):
        if not self.checkpoint.exists():
            return
        try:
            state = json.loads(self.checkpoint.read_text(encoding="utf-8"))
            if state["version"] != 1:
                raise ValueError("unsupported state version")
            for component, value in state["components"].items():
                try:
                    spec = Specification.model_validate(value["specification"])
                    if component != spec.component:
                        raise ValueError("cached identity mismatch")
                    generation = await self.build(spec)
                    self.active[component] = generation
                    self.revisions[component] = value["revision"]
                except Exception as error:
                    self.report(component, "restore", error)
        except Exception as error:
            self.sources_error = f"cache: {type(error).__name__}"

    async def build(self, spec):
        bus = self.event_hub.acquire(spec)
        try:
            return await run_in_threadpool(Generation, spec, self.settings, bus)
        except BaseException:
            await self.event_hub.release(bus)
            raise

    def report(self, component, stage, error):
        # Exception strings can contain URLs, SQL, handler source, or credentials.
        self.errors[component] = f"{stage}: {type(error).__name__}"
        log.warning("component=%s stage=%s error=%s", component, stage, type(error).__name__)

    async def reconcile(self):
        async with self.lock:
            self.monitor.begin()
            success = False
            try:
                success = await self._reconcile()
                return success
            finally:
                self.monitor.finish(success)

    async def _reconcile(self):
        try:
            sources = await self.reader.sources()
        except Exception as error:
            self.sources_error = f"sources unavailable or invalid: {type(error).__name__}"
            return False
        self.sources = {entry.id: {"enabled": entry.enabled, "repository": entry.repository,
                                   "ref": entry.ref if entry.repository else None}
                        for entry in sources.components}
        desired = {entry.id: entry for entry in sources.components if entry.enabled}
        candidates, revisions = {}, dict(self.revisions)
        for component, entry in desired.items():
            try:
                spec, revision = await self.reader.component(entry)
                current = self.active.get(component)
                if current and current.spec == spec:
                    revisions[component] = revision
                else:
                    candidates[component] = await self.build(spec)
                    revisions[component] = revision
                self.errors.pop(component, None)
            except Exception as error:
                self.report(component, "load", error)
        next_active = {key: candidates.get(key, value) for key, value in self.active.items() if key in desired}
        next_active.update(candidates)
        revisions = {key: revisions[key] for key in next_active}
        try:
            await asyncio.to_thread(self.save, next_active, revisions)
        except Exception as error:
            self.sources_error = f"state persistence failed: {type(error).__name__}"
            for candidate in candidates.values():
                await run_in_threadpool(candidate.close)
                await self.event_hub.release(candidate.bus)
            return False
        # No awaits between dispatcher replacement and marking old generations for retirement.
        old = self.active
        self.active, self.revisions = next_active, revisions
        for key, generation in old.items():
            if next_active.get(key) is not generation:
                task = asyncio.create_task(self.retire(generation))
                self.retiring.add(task)
                task.add_done_callback(self.retiring.discard)
        self.errors = {k: v for k, v in self.errors.items() if k in desired}
        self.sources_error = None
        return True

    def save(self, active, revisions):
        def specification(value):
            document = value.spec.model_dump()
            # Keep legacy checkpoints readable by older runtimes unless Redis is opted in.
            if value.spec.realtime.backend == "memory":
                document.pop("realtime")
            return document
        state = {"version": 1, "components": {
            key: {"specification": specification(value), "revision": revisions[key]}
            for key, value in active.items()
        }}
        temporary = self.checkpoint.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as file:
            json.dump(state, file)
            file.flush()
            os.fsync(file.fileno())
        temporary.replace(self.checkpoint)

    async def retire(self, generation):
        try:
            if generation.sockets:
                await asyncio.sleep(self.settings.drain_seconds)
            for socket in tuple(generation.sockets):
                try:
                    await socket.close(code=1012)
                except (RuntimeError, OSError):
                    pass
            while generation.active_requests or generation.sockets:
                await asyncio.sleep(0.05)
        finally:
            await run_in_threadpool(generation.close)
            await self.event_hub.release(generation.bus)

    def status(self):
        return snapshot(self)

    async def poll(self):
        while True:
            await asyncio.sleep(self.settings.poll_seconds)
            try:
                await self.reconcile()
            except Exception as error:
                self.sources_error = f"poll failure: {type(error).__name__}"

    async def start(self):
        await self.restore()
        await self.reconcile()
        self.poll_task = asyncio.create_task(self.poll())

    async def close(self):
        if self.poll_task:
            self.poll_task.cancel()
            await asyncio.gather(self.poll_task, return_exceptions=True)
        # ASGI server has drained requests before lifespan shutdown.
        for task in tuple(self.retiring):
            task.cancel()
        await asyncio.gather(*self.retiring, return_exceptions=True)
        for generation in self.active.values():
            await run_in_threadpool(generation.close)
            await self.event_hub.release(generation.bus)
        await self.event_hub.close()
        await self.reader.close()

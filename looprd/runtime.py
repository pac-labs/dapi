"""Reconcile authoritative sources and atomically persist successful generations."""

import asyncio
import json
import logging
import os

from starlette.concurrency import run_in_threadpool

from .component import Generation
from .events import EventBus, Tickets
from .sources import SourceReader
from .spec import Specification

log = logging.getLogger("looprd")


class Runtime:
    def __init__(self, settings, reader=None):
        self.settings = settings
        self.reader = reader or SourceReader(settings.sources)
        self.active = {}
        self.revisions = {}
        self.errors = {}
        self.sources_error = None
        self.buses = {}
        self.tickets = Tickets()
        self.lock = asyncio.Lock()
        self.retiring = set()
        self.poll_task = None
        settings.data.mkdir(parents=True, exist_ok=True)
        self.checkpoint = settings.data / "active.json"

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
        bus = self.buses.setdefault(spec.component, EventBus(self.settings.event_queue))
        return await run_in_threadpool(Generation, spec, self.settings, bus)

    def report(self, component, stage, error):
        # Exception strings can contain URLs, SQL, handler source, or credentials.
        self.errors[component] = f"{stage}: {type(error).__name__}"
        log.warning("component=%s stage=%s error=%s", component, stage, type(error).__name__)

    async def reconcile(self):
        async with self.lock:
            try:
                sources = await self.reader.sources()
            except Exception as error:
                self.sources_error = f"sources unavailable or invalid: {type(error).__name__}"
                return False
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
        state = {"version": 1, "components": {
            key: {"specification": value.spec.model_dump(), "revision": revisions[key]}
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

    def status(self):
        return {
            "sourcesError": self.sources_error,
            "components": {key: {
                "release": value.spec.release, "revision": self.revisions[key],
                "digest": value.digest, "prefix": value.prefix,
                "schemaVersion": value.spec.database.schemaVersion if value.spec.database else None,
                "authMode": value.spec.auth.mode, "scopes": value.spec.auth.scopes,
                "error": self.errors.get(key),
            } for key, value in self.active.items()},
            "errors": self.errors,
        }

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
        await self.reader.close()

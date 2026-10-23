"""Reconcile authoritative sources and atomically persist successful generations."""

import asyncio
import json
import logging
import os
import time

from starlette.concurrency import run_in_threadpool

from .component import Generation
from .events import Tickets
from .event_hub import EventHub
from .sources import SourceReader
from .spec import Specification
from .status import Monitor, snapshot
from .availability import Availability
from .runtime_guard import RuntimeGuard
from .diagnostics import Diagnostics
from .bugreports import BugReports

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
        self.tree = {}
        self.monitor = Monitor()
        self.buses = {}
        self.event_hub = EventHub(settings, self.buses)
        self.tickets = Tickets()
        self.lock = asyncio.Lock()
        self.retiring = set()
        self.poll_task = None
        settings.data.mkdir(parents=True, exist_ok=True)
        self.guard = RuntimeGuard(settings.data)
        self.diagnostics = Diagnostics(settings.data)
        self.bugreports = BugReports(settings.data)
        self.worker_slots = asyncio.Semaphore(settings.worker_concurrency)
        self.availability = Availability(settings.data)
        self.sockets = {}
        self.checkpoint = settings.data / "active.json"
        self.discovery = None
        if settings.discovery_enabled:
            try:
                from .discovery_observer import DiscoveryObserver
                self.discovery = DiscoveryObserver(self)
            except Exception as error:
                self.record_failure(None, "discovery initialization", error)

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
            from .tree_state import validate_tree
            self.tree = validate_tree(state.get("subcomponents", {}), state["components"])
            for component, value in state["components"].items():
                try:
                    spec = Specification.model_validate(value["specification"])
                    if component != spec.component:
                        raise ValueError("cached identity mismatch")
                    generation = await self.build(spec)
                    from .component_tree import prefix
                    generation.prefix = prefix(component, spec, self.tree)
                    self.active[component] = generation
                    self.revisions[component] = value["revision"]
                except Exception as error:
                    self.report(component, "restore", error)
        except Exception as error:
            self.sources_error = f"cache: {type(error).__name__}"
            self.report_sources("restore", error)

    async def build(self, spec):
        bus = self.event_hub.acquire(spec)
        generation = None
        try:
            generation = await run_in_threadpool(Generation, spec, self.settings, bus)
            generation.runtime = self
            for dependency in spec.dependencies:
                if not dependency.optional:
                    self.settings.secret(dependency.credentialSecret)
            return generation
        except BaseException:
            if generation:
                await generation.shutdown()
            await self.event_hub.release(bus)
            raise

    def report(self, component, stage, error):
        # Exception strings can contain URLs, SQL, handler source, or credentials.
        self.errors[component] = f"{stage}: {type(error).__name__}"
        log.warning("component=%s stage=%s error=%s", component, stage, type(error).__name__)
        self.record_failure(component, stage, error)

    def record_failure(self, component, stage, error):
        try:
            self.diagnostics.record(component, stage, error)
        except Exception:
            log.warning("diagnostic history unavailable")

    async def reconcile(self):
        self.guard.acquire()
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
            self.report_sources("sources", error)
            return False
        self.sources = {entry.id: {"enabled": entry.enabled, "repository": entry.repository,
                                   "ref": entry.ref if entry.repository else None}
                        for entry in sources.components}
        from .component_tree import collect, prefix
        desired, source_revisions, tree, source_errors = await collect(self, sources)
        for component, metadata in tree.items():
            owner = self.sources[metadata["owner"]]
            self.sources[component] = {**owner, "parent": metadata["parent"]}
        candidates, revisions, loaded = {}, dict(self.revisions), set()
        for component, spec in desired.items():
            try:
                revision = source_revisions[component]
                mount = prefix(component, spec, tree)
                if component not in source_errors:
                    loaded.add(component)
                current = self.active.get(component)
                if current and current.spec == spec and current.prefix == mount:
                    revisions[component] = revision
                else:
                    candidates[component] = await self.build(spec)
                    candidates[component].prefix = mount
                    revisions[component] = revision
                self.errors.pop(component, None)
            except Exception as error:
                self.report(component, "load", error)
        for component, error in source_errors.items():
            self.report(component, "load", error)
        from .services import dependency_errors
        prospective = {key: candidates.get(key, value) for key, value in self.active.items() if key in desired}
        prospective.update(candidates)
        failures = dependency_errors(prospective)
        while failures and candidates:
            rejected = set(failures) & candidates.keys()
            for client in set(failures) - rejected:
                rejected.update(item.component for item in prospective[client].spec.dependencies if not item.optional and item.component in candidates)
            if not rejected:
                break
            for component in rejected:
                candidate = candidates.pop(component)
                await candidate.shutdown()
                await self.event_hub.release(candidate.bus)
                self.errors[component] = "load: service dependency incompatible"
                self.report_dependency(component)
                if component in self.revisions:
                    revisions[component] = self.revisions[component]
                else:
                    revisions.pop(component, None)
            prospective = {key: candidates.get(key, value) for key, value in self.active.items() if key in desired}
            prospective.update(candidates)
            failures = dependency_errors(prospective)
        for component in failures:
            self.errors[component] = "dependency: required contract unavailable"
            self.report_dependency(component)
        next_active = {key: candidates.get(key, value) for key, value in self.active.items() if key in desired}
        next_active.update(candidates)
        # A newly discovered child cannot be mounted without its ancestor generations.
        for component, metadata in sorted(tree.items(), key=lambda item: len(item[1]["namespace"])):
            if any(parent not in next_active for parent in metadata["namespace"][:-1]):
                candidate = candidates.pop(component, None)
                if candidate:
                    await candidate.shutdown()
                    await self.event_hub.release(candidate.bus)
                next_active.pop(component, None)
                self.errors[component] = "load: parent component unavailable"
        tree = {key: value for key, value in tree.items() if key in next_active}
        revisions = {key: revisions[key] for key in next_active}
        try:
            await asyncio.to_thread(self.save_tree, next_active, revisions, tree)
        except Exception as error:
            self.sources_error = f"state persistence failed: {type(error).__name__}"
            self.report_sources("checkpoint", error)
            for candidate in candidates.values():
                await candidate.shutdown()
                await self.event_hub.release(candidate.bus)
            return False
        # No awaits between dispatcher replacement and marking old generations for retirement.
        old = self.active
        self.active, self.revisions, self.tree = next_active, revisions, tree
        for generation in self.active.values():
            generation.start()
        for key, generation in old.items():
            if next_active.get(key) is not generation:
                task = asyncio.create_task(self.retire(generation))
                self.retiring.add(task)
                task.add_done_callback(self.retiring.discard)
        self.errors = {k: v for k, v in self.errors.items() if k in desired or k in source_errors}
        if self.discovery:
            try:
                self.discovery.schedule(loaded - self.errors.keys())
            except Exception as error:
                self.record_failure(None, "discovery scheduling", error)
        self.sources_error = None
        return True

    def save(self, active, revisions):
        def specification(value):
            # Default-only additions must not make unchanged legacy checkpoints unreadable.
            return value.spec.model_dump(exclude_defaults=True)
        state = {"version": 1, "components": {
            key: {"specification": specification(value), "revision": revisions[key]}
            for key, value in active.items()
        }}
        tree = getattr(self, "saving_tree", self.tree)
        if tree:
            state["subcomponents"] = tree
        temporary = self.checkpoint.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as file:
            json.dump(state, file)
            file.flush()
            os.fsync(file.fileno())
        temporary.replace(self.checkpoint)

    def save_tree(self, active, revisions, tree):
        self.saving_tree = tree
        try:
            self.save(active, revisions)
        finally:
            del self.saving_tree

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
            await generation.shutdown()
            await self.event_hub.release(generation.bus)

    def status(self):
        return snapshot(self)

    def report_sources(self, stage, error):
        self.record_failure(None, stage, error)

    def report_dependency(self, component):
        self.record_failure(component, "dependency", ValueError("service dependency incompatible"))

    async def poll(self):
        while True:
            self.monitor.next_poll_at = time.time() + self.settings.poll_seconds
            await asyncio.sleep(self.settings.poll_seconds)
            self.monitor.next_poll_at = None
            try:
                await self.reconcile()
            except Exception as error:
                self.sources_error = f"poll failure: {type(error).__name__}"
                self.report_sources("poll", error)

    async def start(self):
        self.guard.acquire()
        try:
            await self.restore()
            if self.discovery:
                self.discovery.start_delivery()
            await self.reconcile()
            self.poll_task = asyncio.create_task(self.poll())
        except BaseException:
            await self.close()
            raise

    async def close(self):
        if self.poll_task:
            self.poll_task.cancel()
            await asyncio.gather(self.poll_task, return_exceptions=True)
        if self.discovery:
            await self.discovery.close()
        # ASGI server has drained requests before lifespan shutdown.
        for task in tuple(self.retiring):
            task.cancel()
        await asyncio.gather(*self.retiring, return_exceptions=True)
        for generation in self.active.values():
            await generation.shutdown()
            await self.event_hub.release(generation.bus)
        await self.event_hub.close()
        await self.reader.close()
        self.guard.close()

"""Compatibility, isolation, failure handling and generation ownership."""

import asyncio
import json
from dataclasses import replace

import pytest
from pydantic import ValidationError

from looprd.config import Settings
from looprd.event_hub import EventHub
from looprd.runtime import Runtime
from looprd.spec import Specification
from redis_fixture import Broker


@pytest.fixture
def broker(monkeypatch):
    broker = Broker()
    monkeypatch.setattr("looprd.redis_events.redis.Redis.from_url", broker.client)
    return broker


async def connected(*buses):
    async with asyncio.timeout(5):
        while not all(bus.bridge.receiving for bus in buses):
            await asyncio.sleep(0.01)


def configured(settings, spec_data):
    (settings.secrets / "redis-url").write_text("redis://looprd:test-only@localhost:6379/0")
    spec_data["realtime"] = {"backend": "redis", "connectionSecret": "redis-url"}
    return Specification.model_validate(spec_data)


def test_v1_compatibility_and_strict_future_options(spec_data):
    assert Specification.model_validate(spec_data).realtime.backend == "memory"
    for realtime in ({"backend": "redis"}, {"backend": "memory", "connectionSecret": "x"},
                     {"backend": "redis", "connectionSecret": "x", "delivery": "durable"},
                     {"backend": "redis", "connectionSecret": "x", "outbox": True}):
        spec_data["realtime"] = realtime
        with pytest.raises(ValidationError):
            Specification.model_validate(spec_data)
    with pytest.raises(ValueError):
        Settings(event_namespace="unsafe:*")


@pytest.mark.asyncio
async def test_legacy_checkpoint_omits_optional_field_and_bad_secret_preserves_serving(setup_project, spec_data):
    settings, write_spec, _ = setup_project
    runtime = Runtime(settings)
    try:
        await runtime.reconcile()
        saved = json.loads(runtime.checkpoint.read_text())
        assert "realtime" not in saved["components"]["demo"]["specification"]
        previous = runtime.active["demo"]
        configured(settings, spec_data)
        (settings.secrets / "redis-url").write_text("https://invalid.example")
        write_spec(spec_data)
        await runtime.reconcile()
        assert runtime.active["demo"] is previous
        assert not runtime.event_hub.bridges
        assert runtime.errors["demo"] == "load: ValueError"
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_fanout_echo_suppression_and_namespace_component_isolation(setup_project, spec_data, broker):
    settings, _, _ = setup_project
    spec = configured(settings, spec_data)
    hubs = [EventHub(settings, {}) for _ in range(2)]
    hubs.append(EventHub(replace(settings, event_namespace="other"), {}))
    buses = [hub.acquire(spec) for hub in hubs]
    other = spec.model_copy(update={"component": "other"})
    other_bus = hubs[1].acquire(other)
    all_buses = [*buses, other_bus]
    try:
        await connected(*all_buses)
        subscribers = [bus.subscribe() for bus in all_buses]
        for subscriber in subscribers:
            subscriber.resources.add("items")
        event = {"component": "demo", "resource": "items", "action": "created", "data": {"id": 1}}
        assert buses[0].publish(event) is None  # ctx.publish remains synchronous.
        assert subscribers[0].queue.get_nowait() == event
        assert await asyncio.wait_for(subscribers[1].queue.get(), 2) == event
        await asyncio.sleep(0.03)
        assert all(subscriber.queue.empty() for subscriber in subscribers)
        assert "test-only" not in str(buses[0].status())
        # Overlapping receiver connections must not duplicate a remote event.
        raw = broker.sent[-1][1]
        buses[1].bridge.deliver(raw)
        assert subscribers[1].queue.empty()
    finally:
        for hub in hubs:
            await hub.close()


@pytest.mark.asyncio
async def test_outage_local_delivery_reconnect_overflow_and_invalid_envelopes(setup_project, spec_data, broker):
    settings, _, _ = setup_project
    settings.event_queue = 1
    hub = EventHub(settings, {})
    bus = hub.acquire(configured(settings, spec_data))
    try:
        await connected(bus)
        subscriber = bus.subscribe()
        subscriber.resources.add("items")
        broker.up = False
        async with asyncio.timeout(2):
            while bus.bridge.receiving:
                await asyncio.sleep(0.02)
        bus.publish({"resource": "items"})
        assert subscriber.queue.get_nowait() == {"resource": "items"}
        assert bus.status()["state"] == "degraded"
        assert bus.status()["error"] == "ConnectionError"
        assert bus.status()["droppedRemoteEvents"] == 1
        broker.up = True
        await connected(bus)
        # No await lets the bounded writer queue fill deterministically.
        bus.publish({"resource": "items"})
        bus.publish({"resource": "items"})
        assert bus.status()["pendingRemoteEvents"] == 1
        assert bus.status()["droppedRemoteEvents"] == 2
        assert subscriber.overflow  # Existing slow-consumer behavior retained.
        for raw in (b"not json", b"[]", b'{"version":2}', b"x" * 1_048_577,
                    json.dumps({"version": 1, "id": "x", "event": {}}).encode()):
            bus.bridge.deliver(raw)
        assert bus.status()["droppedRemoteEvents"] == 7
    finally:
        await hub.close()
    assert all(task.done() for task in bus.bridge.tasks)


@pytest.mark.asyncio
async def test_reload_failed_candidate_checkpoint_and_removal_release_bridges(setup_project, spec_data, broker, monkeypatch):
    settings, write_spec, write_sources = setup_project
    configured(settings, spec_data)
    write_spec(spec_data)
    runtime = Runtime(settings)
    try:
        await runtime.reconcile()
        previous = runtime.active["demo"]
        await connected(previous.bus)
        bridge = previous.bus.bridge
        spec_data["release"] = "2"
        write_spec(spec_data)
        await runtime.reconcile()
        await asyncio.gather(*runtime.retiring)
        assert runtime.active["demo"].bus.bridge is bridge
        assert list(runtime.event_hub.references.values()) == [1]
        # A different secret creates a candidate bridge that must not survive failure.
        (settings.secrets / "redis-url-new").write_text("redis://localhost:6380/0")
        spec_data["realtime"]["connectionSecret"] = "redis-url-new"
        write_spec(spec_data)
        def fail(*args):
            raise OSError("checkpoint failure")
        with monkeypatch.context() as changes:
            changes.setattr(runtime, "save", fail)
            assert not await runtime.reconcile()
        assert len(runtime.event_hub.bridges) == 1
        assert runtime.active["demo"].bus.bridge is bridge
        spec_data["handlers"] = [{"path": "/bad", "kind": "python", "source": "async def handle(ctx): return {}"}]
        write_spec(spec_data)
        await runtime.reconcile()  # Python disabled: candidate construction fails.
        assert len(runtime.event_hub.bridges) == 1
        assert runtime.errors["demo"].startswith("load:")
        write_sources([])
        await runtime.reconcile()
        await asyncio.gather(*runtime.retiring)
        assert not runtime.event_hub.bridges
        assert all(task.done() for task in bridge.tasks)
    finally:
        await runtime.close()

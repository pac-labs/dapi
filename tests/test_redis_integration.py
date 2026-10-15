"""Real Redis protocol/ACL integration; CI provisions an isolated server."""

import asyncio
import os
import secrets

import pytest
import redis.asyncio as redis

from looprd.event_hub import EventHub
from looprd.spec import Specification
from test_redis_events import connected


@pytest.mark.asyncio
async def test_real_redis_fanout_acl_and_no_echo(setup_project, spec_data):
    url = os.getenv("LOOPRD_TEST_REDIS_URL")
    if not url:
        pytest.skip("set LOOPRD_TEST_REDIS_URL to a disposable Redis server")
    settings, _, _ = setup_project
    settings.event_namespace = "test_" + secrets.token_hex(8)
    (settings.secrets / "redis-url").write_text(url)
    spec_data["realtime"] = {"backend": "redis", "connectionSecret": "redis-url"}
    spec = Specification.model_validate(spec_data)
    hubs = [EventHub(settings, {}) for _ in range(2)]
    buses = [hub.acquire(spec) for hub in hubs]
    admin = redis.Redis.from_url(url)
    username, password = "test_" + secrets.token_hex(8), secrets.token_hex(16)
    restricted_hub = None
    try:
        await connected(*buses)
        subscribers = [bus.subscribe() for bus in buses]
        for subscriber in subscribers:
            subscriber.resources.add("items")
        event = {"resource": "items", "data": {"test": True}}
        buses[0].publish(event)
        assert subscribers[0].queue.get_nowait() == event
        assert await asyncio.wait_for(subscribers[1].queue.get(), 5) == event
        await asyncio.sleep(0.1)
        assert subscribers[0].queue.empty() and subscribers[1].queue.empty()
        # Production-style command/channel ACL, without access to Redis keys.
        await admin.execute_command("ACL", "SETUSER", username, "on", ">" + password,
            "&" + settings.event_namespace + ":*", "+ping", "+publish", "+subscribe", "+unsubscribe",
            "+client|setinfo", "+client|setname", "+echo")
        from urllib.parse import urlsplit, urlunsplit
        parsed = urlsplit(url)
        host = parsed.hostname
        if ":" in host:
            host = "[" + host + "]"
        restricted_url = urlunsplit((parsed.scheme, username + ":" + password + "@" + host + ":" + str(parsed.port or 6379), "/0", "", ""))
        (settings.secrets / "redis-restricted").write_text(restricted_url)
        spec.realtime.connectionSecret = "redis-restricted"
        restricted_hub = EventHub(settings, {})
        restricted = restricted_hub.acquire(spec)
        await connected(restricted)
        restricted.publish({"resource": "items", "data": "acl"})
        assert (await asyncio.wait_for(subscribers[1].queue.get(), 5))["data"] == "acl"
    finally:
        if restricted_hub:
            await restricted_hub.close()
        for hub in hubs:
            await hub.close()
        await admin.execute_command("ACL", "DELUSER", username)
        await admin.aclose()

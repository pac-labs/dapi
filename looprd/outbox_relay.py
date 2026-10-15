"""Retry committed SQL notifications; SQL replay is authoritative during outages."""

import asyncio

from sqlalchemy.exc import SQLAlchemyError
from redis.exceptions import RedisError
from starlette.concurrency import run_in_threadpool


async def relay(generation):
    store = generation.storage.events
    while True:
        try:
            for event in await run_in_threadpool(store.pending):
                if generation.bus.bridge:
                    await generation.bus.bridge.publish_confirmed(event["payload"])
                await run_in_threadpool(store.sent, event["sequence"])
            generation.relay_error = None
        except (SQLAlchemyError, RedisError, OSError, ValueError) as error:
            generation.relay_error = type(error).__name__
        await asyncio.sleep(1)

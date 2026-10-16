"""Bounded asynchronous delivery of persistent, content-free wake hints."""

import asyncio
import json

import httpx
from starlette.concurrency import run_in_threadpool

from . import push_transport


async def tick(generation, client):
    runtime, store = generation.runtime, generation.storage.push
    if runtime.active.get(generation.spec.component) is not generation or generation.spec.component in runtime.availability.blocked:
        return False
    job = await run_in_threadpool(store.claim)
    if not job:
        return False
    if job["keyId"] and (generation.storage.keys is None or not await run_in_threadpool(generation.storage.keys.identity, job["keyId"])):
        await run_in_threadpool(store.finish, job, 410)
        return True
    # Recheck after database work; an already issued provider request may finish.
    if runtime.active.get(generation.spec.component) is not generation or generation.spec.component in runtime.availability.blocked:
        return False
    try:
        status = await push_transport.deliver(client, generation.settings, job["endpoint"], json.dumps(job["payload"]).encode())
    except httpx.HTTPError:
        status = 0
    await run_in_threadpool(store.finish, job, status)
    generation.push_error = None if 200 <= status < 300 else "http_" + str(status) if status else "network_error"
    return True


async def worker(generation):
    async with push_transport.client() as client:
        while True:
            try:
                busy = await tick(generation, client)
            except Exception as error:
                generation.push_error = type(error).__name__
                generation.runtime.record_failure(generation.spec.component, "push delivery", error)
                busy = False
            await asyncio.sleep(0.1 if busy else 1)

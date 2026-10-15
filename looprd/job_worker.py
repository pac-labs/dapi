"""At-least-once trusted jobs; applications own idempotent business effects."""

import asyncio
import secrets
from types import SimpleNamespace

from starlette.concurrency import run_in_threadpool

from .auth import Principal
from .process_runner import run


async def execute(generation, job):
    definition = generation.spec.jobs[job["name"]]
    principal = Principal("job:" + job["name"], "server job", frozenset(definition.scopes),
                          kind="service", realm="component-jobs", subject=generation.spec.component)
    timeout = min(definition.timeoutSeconds, generation.settings.request_seconds)
    if definition.execution == "process":
        return await run(generation, definition.source, job["payload"], principal, timeout, definition.secrets, job_id=job["id"])
    module = {"__name__": "looprd_job_" + generation.spec.component}
    exec(compile(definition.source, "<looprd-job>", "exec"), module)
    context = SimpleNamespace(body=job["payload"], principal=principal, job_id=job["id"], enqueued_by=job["owner"],
        specification=generation.spec, session_factory=generation.storage.sessions, secret=generation.settings.secret,
        transaction=generation.storage.events.transaction if generation.storage.events else generation.storage.sessions.begin,
        emit=generation.storage.events.emit if generation.storage.events else None,
        publish=generation.bus.publish, call=generation.call, run=run_in_threadpool)
    async with asyncio.timeout(timeout):
        return await module["handle"](context)


async def worker(generation):
    identity = secrets.token_hex(16)
    store = generation.storage.jobs
    while True:
        try:
            async with generation.runtime.worker_slots:
                job = await run_in_threadpool(store.claim, identity, generation.settings.request_seconds)
                if job:
                    error = None
                    try:
                        await execute(generation, job)
                    except Exception as failure:
                        error = type(failure).__name__
                        generation.runtime.record_failure(generation.spec.component, "job execution", failure)
                    await run_in_threadpool(store.finish, job, identity, error)
            generation.job_error = None
        except Exception as error:
            generation.job_error = type(error).__name__
            generation.runtime.record_failure(generation.spec.component, "job worker", error)
        await asyncio.sleep(1)

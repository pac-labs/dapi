"""Bounded subprocess I/O with wall-clock cancellation and forced cleanup."""

import asyncio
import json
import sys
import os

from .outbox import EventError


async def read(stream, limit, retain=True):
    data, size = [], 0
    while chunk := await stream.read(65536):
        size += len(chunk)
        if size > limit:
            raise EventError(502, "worker output exceeded its bound")
        if retain:
            data.append(chunk)
    return b"".join(data)


async def run(generation, source, body, principal, timeout, secrets=(), request=None, job_id=None):
    principal_data = {"id": principal.id, "kind": principal.kind, "realm": principal.realm,
        "subject": principal.subject, "scopes": list(principal.scopes), "session_id": principal.session_id}
    data = {"source": source, "body": body, "principal": principal_data, "timeout": timeout,
        "memory": generation.settings.worker_memory_mb, "specification": generation.spec.model_dump(),
        "request": request or {}, "secrets": {name: generation.settings.secret(name) for name in secrets}, "jobId": job_id}
    raw = json.dumps(data).encode()
    if len(raw) > 2097151:
        raise EventError(413, "worker input exceeds limit")
    process = await asyncio.create_subprocess_exec(sys.executable, "-m", "looprd.worker",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        **({"creationflags": 0x08000000} if os.name == "nt" else {}))
    tasks = [asyncio.create_task(read(process.stdout, 1048576)),
             asyncio.create_task(read(process.stderr, 65536, False))]
    try:
        async with asyncio.timeout(timeout):
            process.stdin.write(raw)
            await process.stdin.drain()
            process.stdin.close()
            output, _ = await asyncio.gather(*tasks)
            await process.wait()
        if process.returncode:
            raise EventError(502, "bounded worker failed")
        return json.loads(output)
    except TimeoutError:
        raise EventError(504, "bounded worker deadline exceeded") from None
    finally:
        if process.returncode is None:
            process.kill()
        await process.wait()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

"""Serialize metadata changes and stream bounded uploads outside the event loop."""

import asyncio
import logging
import os
import time

from sqlalchemy.exc import SQLAlchemyError

from .file_store import FileError, FileStore


def seal(file):
    file.flush()
    os.fsync(file.fileno())
    file.close()


async def disk(function, *args):
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


class FileService:
    def __init__(self, store):
        self.store = store
        self.lock = asyncio.Lock()
        self.next_sweep_at = time.time() + 30
        self.sweep_state = "waiting"
        self.sweep_error = None
        self.task = asyncio.create_task(self.sweep())

    async def call(self, operation, *args):
        async with self.lock:
            return await disk(getattr(self.store, operation), *args)

    async def upload(self, token, request):
        row = await self.call("claim", token)
        size, file = 0, None
        try:
            # A raw PUT represents exactly one file; multipart parsing is unnecessary.
            file = self.store.path(row["id"]).open("xb")
            async with asyncio.timeout(min(300, max(0.001, row["expires"] - time.time()))):
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > self.store.limit:
                        raise FileError(413, "file exceeds maximum size; request a new invitation")
                    await disk(file.write, chunk)
            await disk(seal, file)
            file = None
            return await self.call("finish", row["id"], size)
        except BaseException:
            if file is not None:
                await disk(file.close)
            await self.call("fail", row["id"])
            raise

    async def sweep(self):
        while True:
            self.next_sweep_at = time.time() + 30
            self.sweep_state = "error" if self.sweep_error else "waiting"
            await asyncio.sleep(30)
            self.next_sweep_at = None
            self.sweep_state = "running"
            try:
                await self.call("clean")
                self.sweep_error = None
            except (OSError, SQLAlchemyError):
                self.sweep_error = "cleanup_unavailable"
                logging.getLogger("looprd").warning("file expiry cleanup failed; retrying")

    async def close(self):
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)
        await disk(self.store.close)


async def start_files(settings):
    return FileService(await disk(FileStore, settings))

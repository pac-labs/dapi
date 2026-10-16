"""Read only the host's bounded, sanitized container metrics snapshot."""

import json
import time
from typing import Literal

from pydantic import Field, ValidationError

from .contract import Strict

NAMES = ("looprd", "looprd-postgres", "looprd-redis", "looprd-ntfy")


class ContainerMetric(Strict):
    name: Literal["looprd", "looprd-postgres", "looprd-redis", "looprd-ntfy"]
    state: Literal["running", "exited", "paused", "created", "stopped", "unknown", "missing"]
    startedAt: str | None = Field(default=None, max_length=64)
    restarts: int | None = Field(default=None, ge=0)
    cpuPercent: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    cpuTotalSeconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    memoryBytes: int | None = Field(default=None, ge=0)
    memoryLimitBytes: int | None = Field(default=None, ge=0)
    networkRxBytes: int | None = Field(default=None, ge=0)
    networkTxBytes: int | None = Field(default=None, ge=0)
    blockReadBytes: int | None = Field(default=None, ge=0)
    blockWriteBytes: int | None = Field(default=None, ge=0)
    pids: int | None = Field(default=None, ge=0)
    error: Literal["stats-unavailable", "inspect-unavailable"] | None = None


class Snapshot(Strict):
    version: Literal[1]
    collectedAt: float = Field(ge=0, allow_inf_nan=False)
    memorySupported: bool
    containers: list[ContainerMetric] = Field(max_length=4)


def read(settings):
    path = settings.container_metrics_file
    if path is None:
        return {"state": "disabled", "containers": []}
    try:
        with path.open("rb") as source:
            raw = source.read(65537)
        if len(raw) > 65536:
            raise ValueError("snapshot exceeds limit")
        snapshot = Snapshot.model_validate_json(raw)
        if len({item.name for item in snapshot.containers}) != len(snapshot.containers):
            raise ValueError("duplicate metrics")
        age = time.time() - snapshot.collectedAt
        if age < -5:
            raise ValueError("snapshot timestamp is in the future")
        result = snapshot.model_dump()
        result.update(state="stale" if age > 60 else "available", ageSeconds=max(0, int(age)))
        return result
    except FileNotFoundError:
        return {"state": "unavailable", "containers": []}
    except (OSError, ValueError, ValidationError):
        # Never expose file contents, host paths, environment or collector errors.
        return {"state": "invalid", "containers": []}

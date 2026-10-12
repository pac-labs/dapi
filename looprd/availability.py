"""Persistent operator overrides, independent of watched component specifications."""

import json
import os
from typing import Literal

from pydantic import Field, model_validator

from .spec import Name, Strict


class AvailabilityState(Strict):
    version: Literal[1] = 1
    blocked: list[Name] = Field(default_factory=list, max_length=4096)

    @model_validator(mode="after")
    def unique(self):
        if len(set(self.blocked)) != len(self.blocked):
            raise ValueError("duplicate blocked component")
        return self


class Availability:
    def __init__(self, data):
        self.path = data / "availability.json"
        self.blocked = frozenset()
        if self.path.exists():
            state = AvailabilityState.model_validate_json(self.path.read_bytes())
            self.blocked = frozenset(state.blocked)

    def save(self, blocked):
        state = AvailabilityState(blocked=sorted(blocked))
        temporary = self.path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as file:
            json.dump(state.model_dump(), file)
            file.flush()
            os.fsync(file.fileno())
        temporary.replace(self.path)

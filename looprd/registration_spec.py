"""Opaque optional service interests, never runtime dependencies."""

import re

from pydantic import Field, field_validator

from .contract import Strict, Name


class Registration(Strict):
    service: Name
    document: str = Field(min_length=1, max_length=256)

    @field_validator("document")
    @classmethod
    def relative_document(cls, value):
        if (not re.fullmatch(r"[A-Za-z0-9_./-]+", value)
                or any(part in ("", ".", "..") or part.startswith(".") for part in value.split("/"))):
            raise ValueError("registration document must be a plain relative descendant path")
        return value

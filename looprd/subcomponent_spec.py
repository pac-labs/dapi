"""Portable independent child declarations; infrastructure is never inherited."""

from pydantic import Field, field_validator

from .contract import Strict


class Subcomponent(Strict):
    source: str = Field(min_length=1, max_length=512)

    @field_validator("source")
    @classmethod
    def descendant(cls, value):
        from .spec_imports import child_location
        child_location("https://example.invalid/looprd.yaml", value)
        return value

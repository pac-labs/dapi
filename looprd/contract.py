"""Shared strict contract primitives."""

from typing import Annotated
from pydantic import BaseModel, ConfigDict, StringConstraints

Name = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]{0,47}$")]
Symbol = Annotated[str, StringConstraints(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,63}$")]
PathName = Annotated[str, StringConstraints(pattern=r"^/(?:[a-zA-Z0-9_-]+/?)*$")]
Scope = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9:._-]{0,95}$")]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")



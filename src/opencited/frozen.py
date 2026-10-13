"""The base of every model in the package."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Frozen(BaseModel):
    """A strict model that cannot change after it is built.

    Strict validation coerces nothing, so a ``float`` can never stand in for a
    ``Decimal``, and models hold tuples instead of lists or dicts, so nothing inside one
    can change either.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

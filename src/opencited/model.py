"""The base of every model in the package, and the field types that models share."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, StringConstraints


class Frozen(BaseModel):
    """A strict model that cannot change after it is built.

    In Python mode strict validation coerces nothing. Decimal fields use
    ``opencited.values.ExactDecimal``, which also refuses binary numbers in JSON input.
    By convention models hold tuples, never lists or dicts, so nothing inside one can
    change either.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


def _require_utc(moment: datetime) -> datetime:
    if moment.utcoffset() != timedelta(0):
        raise ValueError("the time must be in UTC")
    return moment


TableId = Annotated[str, StringConstraints(pattern=r"^[0-9]{5}$")]
"""A StatBank table ID: five ASCII digits."""

Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
"""A SHA-256 digest in lower-case hexadecimal."""

UtcDatetime = Annotated[AwareDatetime, AfterValidator(_require_utc)]
"""A time with a time zone, which must be UTC."""

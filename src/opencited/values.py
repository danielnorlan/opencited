"""Exact values from sources: JSON is parsed with decimals and never with floats (ADR 0009)."""

from __future__ import annotations

import json
from decimal import Decimal


def _reject_constant(name: str) -> object:
    raise ValueError(f"the JSON holds {name}, which is not a finite number")


def load_json(raw: bytes) -> object:
    """Parse JSON from the raw bytes of a response, with every number as a ``Decimal``.

    Parsing the bytes that are hashed for the provenance record keeps each value exactly
    as the source wrote it. NaN, infinities, numbers that ``Decimal`` cannot hold and
    nesting too deep to parse all raise ``ValueError``.
    """
    try:
        return json.loads(
            raw.decode("utf-8"),
            parse_float=Decimal,
            parse_int=Decimal,
            parse_constant=_reject_constant,
        )
    except (ArithmeticError, RecursionError) as error:
        raise ValueError(f"the JSON could not be parsed: {error}") from error

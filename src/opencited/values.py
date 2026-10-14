"""Exact values from sources: JSON is parsed with decimals and never with floats (ADR 0009)."""

from __future__ import annotations

import json
import re
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BeforeValidator, PlainSerializer, WithJsonSchema

DECIMAL_PATTERN = r"^-?[0-9]+(\.[0-9]+)?$"


def _reject_constant(name: str) -> object:
    raise ValueError(f"the JSON holds {name}, which is not a finite number")


def load_json(raw: bytes) -> object:
    """Parse JSON from the body of a response, with every number as a ``Decimal``.

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


def exact_text(value: Decimal) -> str:
    """Write a decimal in fixed point, with every digit and the scale as written.

    ``str`` would write some decimals in exponent form, such as ``0E-7`` for
    ``0.0000000``.
    """
    return format(value, "f")


# Separators of the formatter. Norwegian text groups digits with a no-break space and
# writes the minus sign U+2212; English text groups digits with commas and writes a
# hyphen-minus. The evidence gate reads these and the other forms of spec 0003.
NO_BREAK_SPACE = "\u00a0"
MINUS_SIGN = "\u2212"
SEPARATORS = {
    "no": (NO_BREAK_SPACE, ",", MINUS_SIGN),
    "en": (",", ".", "-"),
}
# The integer part is grouped only from this many digits, so years and other numbers
# of four digits keep their usual form.
GROUP_FROM = 5


def format_number(value: Decimal, language: Literal["no", "en"]) -> str:
    """Write a decimal for text in Norwegian or English, with its scale as it is.

    ``Decimal("1234567.50")`` becomes ``1 234 567,50`` in Norwegian, with no-break
    spaces, and ``1,234,567.50`` in English.
    """
    group, point, minus = SEPARATORS[language]
    # copy_abs is exact; abs() would round to the context's 28 digits.
    text = exact_text(value.copy_abs())
    whole, _, fraction = text.partition(".")
    if len(whole) >= GROUP_FROM:
        first = len(whole) % 3 or 3
        parts = [whole[:first]] + [
            whole[index : index + 3] for index in range(first, len(whole), 3)
        ]
        whole = group.join(parts)
    sign = minus if value < 0 and not value.is_zero() else ""
    return f"{sign}{whole}{point}{fraction}" if fraction else f"{sign}{whole}"


def _exact_input(value: object) -> object:
    """Refuse binary numbers, and read a decimal string, such as this module writes, exactly."""
    if isinstance(value, bool | int | float):
        raise ValueError("a value must be a Decimal or a decimal string, not a binary number")
    if isinstance(value, str):
        if not re.fullmatch(DECIMAL_PATTERN, value):
            raise ValueError("a decimal string has digits, an optional point and an optional minus")
        return Decimal(value)
    return value


ExactDecimal = Annotated[
    Decimal,
    BeforeValidator(_exact_input),
    PlainSerializer(exact_text, return_type=str, when_used="json"),
    WithJsonSchema({"type": "string", "pattern": DECIMAL_PATTERN}),
]
"""A decimal that is written as a fixed-point string in JSON and never read from a float."""

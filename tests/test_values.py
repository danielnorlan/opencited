"""Tests for parsing JSON with exact decimals."""

from __future__ import annotations

from decimal import Decimal
from typing import cast

import pytest

from opencited.values import format_number, load_json


def test_numbers_become_decimals_with_their_written_scale() -> None:
    parsed = load_json(b'{"a": 1.10, "b": 2, "c": -0.000001, "d": 123456789012345678901234567890}')
    assert parsed == {
        "a": Decimal("1.10"),
        "b": Decimal(2),
        "c": Decimal("-0.000001"),
        "d": Decimal("123456789012345678901234567890"),
    }
    assert isinstance(parsed, dict)
    assert str(cast("dict[str, Decimal]", parsed)["a"]) == "1.10"


@pytest.mark.parametrize("constant", [b"NaN", b"Infinity", b"-Infinity"])
def test_constants_that_are_not_finite_numbers_are_rejected(constant: bytes) -> None:
    with pytest.raises(ValueError, match="not a finite number"):
        load_json(b"[" + constant + b"]")


def test_bytes_that_are_not_utf8_are_rejected() -> None:
    with pytest.raises(ValueError, match="utf-8"):
        load_json(b"\xff\xfe")


@pytest.mark.parametrize(
    ("value", "norwegian", "english"),
    [
        ("1234567.50", "1\u00a0234\u00a0567,50", "1,234,567.50"),
        ("-12345", "\u221212\u00a0345", "-12,345"),
        ("2026", "2026", "2026"),
        ("1234.5", "1234,5", "1234.5"),
        ("0.059", "0,059", "0.059"),
        ("-0.000", "0,000", "0.000"),
        # Thirty digits, more than the 28 of Python's decimal context.
        (
            "123456789012345678901234567890",
            "123\u00a0456\u00a0789\u00a0012\u00a0345\u00a0678\u00a0901\u00a0234\u00a0567\u00a0890",
            "123,456,789,012,345,678,901,234,567,890",
        ),
    ],
)
def test_numbers_are_formatted_for_each_language(value: str, norwegian: str, english: str) -> None:
    assert format_number(Decimal(value), "no") == norwegian
    assert format_number(Decimal(value), "en") == english

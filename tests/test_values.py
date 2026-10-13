"""Tests for parsing JSON with exact decimals."""

from __future__ import annotations

from decimal import Decimal
from typing import cast

import pytest

from opencited.values import load_json


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

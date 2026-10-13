"""Decimals at the JSON boundary: written in fixed point, never read from a float (ADR 0009)."""

from __future__ import annotations

import json
import re
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from opencited.sources import Observation
from opencited.values import DECIMAL_PATTERN, exact_text


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (Decimal("0.0000000"), "0.0000000"),
        (Decimal("1E-7"), "0.0000001"),
        (Decimal("1.5E+3"), "1500"),
        (Decimal("-0.10"), "-0.10"),
        (Decimal("5594340"), "5594340"),
    ],
)
def test_values_are_written_in_fixed_point(value: Decimal, text: str) -> None:
    cell = Observation(codes=("x",), value=value, symbol=None)
    assert json.loads(cell.model_dump_json())["value"] == text
    assert exact_text(value) == text


@settings(deadline=None)
@given(
    st.integers(min_value=0, max_value=12).flatmap(
        lambda places: st.decimals(
            min_value=Decimal("-1e30"),
            max_value=Decimal("1e30"),
            allow_nan=False,
            allow_infinity=False,
            places=places,
        )
    )
)
def test_the_written_form_keeps_value_and_scale(value: Decimal) -> None:
    text = json.loads(Observation(codes=("x",), value=value, symbol=None).model_dump_json())[
        "value"
    ]
    assert re.fullmatch(DECIMAL_PATTERN, text)
    assert Decimal(text) == value
    assert Decimal(text).as_tuple().exponent == value.as_tuple().exponent


@pytest.mark.parametrize("number", ["0.12345678901234567890123", "1.5", "7", "true"])
def test_a_binary_number_in_json_is_refused(number: str) -> None:
    with pytest.raises(ValidationError, match="not a binary number"):
        Observation.model_validate_json(f'{{"codes":["a"],"value":{number},"symbol":null}}')


@pytest.mark.parametrize("text", ["1E-7", "0x10", "1,5", " 1"])
def test_a_string_that_is_not_a_fixed_point_decimal_is_refused(text: str) -> None:
    with pytest.raises(ValidationError, match="decimal string"):
        Observation.model_validate_json(f'{{"codes":["a"],"value":"{text}","symbol":null}}')


def test_the_json_form_reads_back_to_the_same_cell() -> None:
    cell = Observation(codes=("a",), value=Decimal("0.0000000"), symbol=None)
    again = Observation.model_validate_json(cell.model_dump_json())
    assert again == cell
    assert again.value is not None
    assert again.value.as_tuple().exponent == -7


def test_a_decimal_string_in_json_is_read_exactly() -> None:
    cell = Observation.model_validate_json(
        '{"codes":["a"],"value":"0.12345678901234567890123","symbol":null}'
    )
    assert cell.value == Decimal("0.12345678901234567890123")


@pytest.mark.parametrize("value", [1.5, 7])
def test_a_binary_number_in_python_is_refused(value: float) -> None:
    with pytest.raises(ValidationError):
        Observation.model_validate({"codes": ("a",), "value": value, "symbol": None})


def test_the_schema_declares_a_decimal_string() -> None:
    schema = Observation.model_json_schema(mode="serialization")
    value = schema["properties"]["value"]
    assert {"type": "string", "pattern": DECIMAL_PATTERN} in value["anyOf"]

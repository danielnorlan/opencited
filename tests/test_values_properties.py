"""Property-based tests of decimal parsing (spec 0001, verification 1)."""

from __future__ import annotations

from decimal import Decimal
from typing import cast

from hypothesis import given, settings
from hypothesis import strategies as st

from opencited.values import load_json

FINITE = st.decimals(allow_nan=False, allow_infinity=False)


@settings(deadline=None)
@given(FINITE)
def test_a_number_keeps_its_value_and_its_written_form(value: Decimal) -> None:
    parsed = cast("list[Decimal]", load_json(f"[{value}]".encode()))
    assert parsed == [value]
    assert [str(item) for item in parsed] == [str(value)]


@settings(deadline=None)
@given(
    st.integers(min_value=0, max_value=12).flatmap(
        lambda places: st.tuples(st.just(places), st.integers(-(10**20), 10**20))
    )
)
def test_trailing_zeros_survive(scaled: tuple[int, int]) -> None:
    places, units = scaled
    text = f"{Decimal(units).scaleb(-places):f}"
    parsed = cast("list[Decimal]", load_json(f"[{text}]".encode()))
    # str() would write 0.0000000 as 0E-7, so the written form is compared in fixed point.
    assert [f"{item:f}" for item in parsed] == [text]
    assert parsed[0].as_tuple().exponent == -places

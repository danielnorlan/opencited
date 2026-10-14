"""Property-based tests: every operation against exact rational arithmetic (spec 0002).

The values are random decimals of up to 38 digits, which stand for no response from
SSB. The engine runs on snapshots that hold them, made from a recorded table's cells.
"""

from __future__ import annotations

import json
from decimal import Decimal
from fractions import Fraction
from typing import Any

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st
from snapshotting import altered, population

from opencited.calc import CalculationError, Engine, Plan, arithmetic
from opencited.snapshots import SnapshotStore, digits, scale_of

DIGITS = 38
CELLS = 63
SEX, AGE, CONTENT = "0", "999B", "Personer"


def away(value: Fraction, decimals: int) -> Decimal:
    """Round by a formula of its own: half of the last place, then down, away from zero."""
    scaled = abs(value) * 10**decimals
    whole = (2 * scaled.numerator + scaled.denominator) // (2 * scaled.denominator)
    return Decimal(f"{'-' if value < 0 and whole else ''}{whole}E-{decimals}")


@st.composite
def decimals(draw: st.DrawFn, scale: int | None = None) -> Decimal:
    """A decimal of up to 38 digits, with ``scale`` decimals or a scale of its own."""
    places = draw(st.integers(0, 12)) if scale is None else scale
    whole = draw(st.integers(-(10**DIGITS - 1), 10**DIGITS - 1))
    return Decimal(f"{whole}E-{places}")


@given(st.fractions(), st.integers(0, 20))
def test_rounding_has_ties_away_from_zero_at_the_decimals_asked(
    value: Fraction, places: int
) -> None:
    rounded = arithmetic.round_away(value, places)
    assert rounded == away(value, places)
    # A rounded zero has no sign, which equality of decimals would hide.
    assert not (rounded.is_zero() and rounded.is_signed())
    assert rounded.as_tuple().exponent == -places
    assert abs(Fraction(rounded) - value) <= Fraction(1, 2 * 10**places)


@given(st.integers(-(10**12), 10**12), st.integers(0, 20))
def test_a_tie_rounds_away_from_zero(whole: int, places: int) -> None:
    tie = Fraction(2 * whole + 1, 2 * 10**places)
    rounded = Fraction(arithmetic.round_away(tie, places))
    assert abs(rounded) > abs(tie)


@given(st.lists(decimals(), min_size=1, max_size=20))
def test_sums_of_decimals_end_and_are_exact(values: list[Decimal]) -> None:
    total = arithmetic.total(values)
    assert total == sum((Fraction(value) for value in values), Fraction(0))
    written = arithmetic.exact_decimal(total)
    assert written is not None
    assert Fraction(written) == total


@given(st.integers(1, 10**6), st.integers(1, 10**6))
def test_a_repeating_fraction_has_no_exact_decimal(numerator: int, denominator: int) -> None:
    value = Fraction(numerator, denominator)
    rest = value.denominator
    for prime in (2, 5):
        while rest % prime == 0:
            rest //= prime
    assert (arithmetic.exact_decimal(value) is None) == (rest != 1)


def written(number: Fraction | Decimal) -> Decimal:
    """Return an expected result as the decimal that the engine must give."""
    if isinstance(number, Decimal):
        return number
    exact = arithmetic.exact_decimal(number)
    assert exact is not None
    return exact


@pytest.fixture(scope="module")
def store(tmp_path_factory: pytest.TempPathFactory) -> SnapshotStore:
    return SnapshotStore(tmp_path_factory.mktemp("snapshots"))


def cell(name: str, snapshot: str, year: str) -> dict[str, Any]:
    codes = {"Kjonn": SEX, "Alder": AGE, "ContentsCode": CONTENT, "Tid": year}
    return {"name": name, "operation": "value", "snapshot": snapshot, "codes": codes}


@settings(max_examples=40, deadline=None)
@given(
    scale=st.integers(0, 8),
    data=st.data(),
    places=st.integers(0, 10),
)
def test_the_engine_computes_every_operation_exactly(
    store: SnapshotStore, scale: int, data: st.DataObject, places: int
) -> None:
    values = data.draw(st.lists(decimals(scale), min_size=CELLS, max_size=CELLS))
    base = population()
    snapshot = store.write(altered(base, values=values)).name
    position = {cell.codes: index for index, cell in enumerate(base.result.observations)}
    a, b, c = (values[position[(SEX, AGE, CONTENT, year)]] for year in ("2024", "2025", "2026"))
    assume(b != 0 and a != 0)
    years = {
        "Kjonn": [SEX],
        "Alder": [AGE],
        "ContentsCode": [CONTENT],
        "Tid": ["2024", "2025", "2026"],
    }
    steps: list[dict[str, Any]] = [
        cell("a", snapshot, "2024"),
        cell("b", snapshot, "2025"),
        cell("c", snapshot, "2026"),
        {"name": "total", "operation": "sum", "snapshot": snapshot, "selection": years},
        {
            "name": "mean",
            "operation": "mean",
            "snapshot": snapshot,
            "selection": years,
            "decimals": places,
        },
        {"name": "plus", "operation": "add", "left": "a", "right": "b"},
        {"name": "minus", "operation": "subtract", "left": "c", "right": "a"},
        {"name": "quotient", "operation": "divide", "left": "c", "right": "b", "decimals": places},
        {
            "name": "change",
            "operation": "change_percent",
            "old": "a",
            "new": "c",
            "decimals": places,
        },
        {
            "name": "share",
            "operation": "share_percent",
            "part": "c",
            "total": "b",
            "decimals": places,
        },
        {"name": "times", "operation": "multiply", "left": "a", "right": "c"},
    ]
    fa, fb, fc = Fraction(a), Fraction(b), Fraction(c)
    expected = {
        "total": fa + fb + fc,
        "mean": away((fa + fb + fc) / 3, places),
        "plus": fa + fb,
        "minus": fc - fa,
        "quotient": away(fc / fb, places),
        "change": away((fc - fa) / fa * 100, places),
        "share": away(fc / fb * 100, places),
        "times": fa * fc,
    }
    # A step whose result needs more than 38 digits must fail; the others must match.
    too_wide = [
        name
        for name, number in expected.items()
        if digits(written(number), scale_of(written(number))) > DIGITS
    ]
    plan = Plan.model_validate_json(json.dumps({"steps": steps}))
    if too_wide:
        with pytest.raises(CalculationError, match="more than 38"):
            Engine(store).run(plan)
        return
    result = Engine(store).run(plan)
    for name, number in expected.items():
        assert Fraction(result.step(name).value or 0) == Fraction(number), name
        if isinstance(number, Decimal):
            assert result.step(name).value == number, name


@settings(max_examples=30, deadline=None)
@given(
    ages=st.lists(st.integers(-5, 5), min_size=7, max_size=7),
    order=st.sampled_from(["highest", "lowest"]),
    count=st.integers(1, 7),
)
def test_a_ranking_keeps_the_best_cells_and_their_ties(
    store: SnapshotStore, ages: list[int], order: str, count: int
) -> None:
    # Few distinct values, so that ties are common.
    base = population()
    codes = [value.code for value in base.result.dimensions[1].values]
    numbers = dict(zip(codes, ages, strict=True))
    values: list[Decimal | None] = [
        Decimal(numbers[cell.codes[1]])
        if cell.codes[0] == SEX and cell.codes[3] == "2026"
        else Decimal(0)
        for cell in base.result.observations
    ]
    snapshot = store.write(altered(base, values=values)).name
    step: dict[str, Any] = {
        "name": "ranked",
        "operation": "rank",
        "snapshot": snapshot,
        "selection": {"Kjonn": [SEX], "ContentsCode": [CONTENT], "Tid": ["2026"]},
        "by": "Alder",
        "order": order,
        "count": count,
    }
    result = Engine(store).run(Plan.model_validate_json(json.dumps({"steps": [step]})))

    def place(number: int) -> int:
        ahead = [
            other for other in ages if (other > number if order == "highest" else other < number)
        ]
        return 1 + len(ahead)

    expected = {code: place(number) for code, number in numbers.items() if place(number) <= count}
    ranked = result.step("ranked").ranked
    assert {cell.codes[1]: cell.position for cell in ranked} == expected

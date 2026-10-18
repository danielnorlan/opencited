"""The calculation engine over snapshots of recorded responses.

Expected numbers come from the recordings in `tests/fixtures/ssb/`, computed here with
`Fraction`, never written by hand. Tests that need values, units or codes that no
recording holds use `altered`, whose snapshots stand for no response from SSB.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any, cast

import duckdb
import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError
from recorded import recorded_cells
from snapshotting import altered, catch, population, symbols, wide

from opencited.calc import (
    CalculationError,
    CalculationResult,
    Engine,
    Plan,
    parse_plan,
    plan_schema,
)
from opencited.calc.engine import (
    bound_codes,
    check_types,
    locked,
    select_statement,
    unit_named,
    unit_scale,
)
from opencited.snapshots import MANIFEST, OBSERVATIONS, SnapshotStore
from opencited.sources import Unit

POPULATION = "data-05810-expressions-en"
TOTAL = {"Kjonn": "0", "Alder": "999B", "ContentsCode": "Personer"}
TOTAL_CODES = {code: [item] for code, item in TOTAL.items()}


def recorded(name: str, *codes: str) -> Decimal:
    """Return the value of a recorded cell, as SSB wrote it."""
    [token] = [token for cell, token, _ in recorded_cells(name) if cell == codes]
    return Decimal(token)


def away(value: Fraction, decimals: int) -> Decimal:
    """Round as the engine must, by a formula of its own: ties away from zero."""
    scaled = abs(value) * 10**decimals
    whole = (2 * scaled.numerator + scaled.denominator) // (2 * scaled.denominator)
    return Decimal(f"{'-' if value < 0 and whole else ''}{whole}E-{decimals}")


@pytest.fixture
def store(tmp_path: Path) -> SnapshotStore:
    return SnapshotStore(tmp_path)


def run(store: SnapshotStore, *steps: dict[str, Any]) -> CalculationResult:
    return Engine(store).run(Plan.model_validate_json(json.dumps({"steps": list(steps)})))


def value(name: str, snapshot: str, year: str, sex: str = "0", age: str = "999B") -> dict[str, Any]:
    codes = {"Kjonn": sex, "Alder": age, "ContentsCode": "Personer", "Tid": year}
    return {"name": name, "operation": "value", "snapshot": snapshot, "codes": codes}


def test_every_operation_matches_exact_arithmetic_on_recorded_values(store: SnapshotStore) -> None:
    people = store.write(population()).name
    result = run(
        store,
        value("now", people, "2026"),
        value("before", people, "2025"),
        value("women", people, "2026", sex="2"),
        {
            "name": "growth",
            "operation": "change_percent",
            "old": "before",
            "new": "now",
            "decimals": 2,
        },
        {
            "name": "share",
            "operation": "share_percent",
            "part": "women",
            "total": "now",
            "decimals": 1,
        },
        {"name": "more", "operation": "subtract", "left": "now", "right": "before"},
        {"name": "both", "operation": "add", "left": "now", "right": "before"},
        {"name": "ratio", "operation": "divide", "left": "now", "right": "before", "decimals": 6},
        {
            "name": "sexes",
            "operation": "sum",
            "snapshot": people,
            "selection": {
                "Kjonn": ["1", "2"],
                "Alder": ["999B"],
                "ContentsCode": ["Personer"],
                "Tid": ["2026"],
            },
        },
        {
            "name": "average",
            "operation": "mean",
            "snapshot": people,
            "selection": {
                "Kjonn": ["0"],
                "Alder": ["999B"],
                "ContentsCode": ["Personer"],
                "Tid": ["2024", "2025", "2026"],
            },
            "decimals": 1,
        },
    )
    now = Fraction(recorded(POPULATION, "0", "999B", "Personer", "2026"))
    before = Fraction(recorded(POPULATION, "0", "999B", "Personer", "2025"))
    women = Fraction(recorded(POPULATION, "2", "999B", "Personer", "2026"))
    men = Fraction(recorded(POPULATION, "1", "999B", "Personer", "2026"))
    years = [
        Fraction(recorded(POPULATION, "0", "999B", "Personer", year))
        for year in ("2024", "2025", "2026")
    ]
    expected = {
        "now": Decimal(now.numerator),
        "growth": away((now - before) / before * 100, 2),
        "share": away(women / now * 100, 1),
        "more": Decimal((now - before).numerator),
        "both": Decimal((now + before).numerator),
        "ratio": away(now / before, 6),
        "sexes": Decimal((men + women).numerator),
        "average": away(sum(years, Fraction(0)) / 3, 1),
    }
    for name, number in expected.items():
        assert result.step(name).value == number, name
    assert (
        result.step("growth").exact
        == f"{((now - before) / before * 100).numerator}/{((now - before) / before * 100).denominator}"
    )


def test_decimals_from_the_recording_are_summed_exactly(store: SnapshotStore) -> None:
    fish = store.write(catch()).name
    selection = {
        "FiskeslagSamiske": ["80a"],
        "SamiskeElver": ["18"],
        "ContentsCode": ["Gjennomsnitt"],
        "Tid": ["2015", "2016"],
    }
    result = run(
        store,
        {"name": "total", "operation": "sum", "snapshot": fish, "selection": selection},
        {
            "name": "mean",
            "operation": "mean",
            "snapshot": fish,
            "selection": selection,
            "decimals": 3,
        },
    )
    cells = [
        Fraction(recorded("data-06743-decimals-no", "80a", "18", "Gjennomsnitt", year))
        for year in ("2015", "2016")
    ]
    assert Fraction(result.step("total").value or 0) == sum(cells, Fraction(0))
    assert result.step("mean").value == away(sum(cells, Fraction(0)) / 2, 3)
    assert result.step("total").unit.name == "kg"


def test_a_ranking_includes_cells_tied_with_the_last_one(store: SnapshotStore) -> None:
    people = store.write(population()).name
    result = run(
        store,
        {
            "name": "largest",
            "operation": "rank",
            "snapshot": people,
            "selection": {"Kjonn": ["0"], "ContentsCode": ["Personer"], "Tid": ["2026"]},
            "by": "Alder",
            "order": "highest",
            "count": 2,
        },
    )
    ages = [
        (cell[1], Decimal(token))
        for cell, token, _ in recorded_cells(POPULATION)
        if cell[0] == "0" and cell[3] == "2026"
    ]
    ordered = sorted(ages, key=lambda item: item[1], reverse=True)
    assert [cell.codes[1] for cell in result.step("largest").ranked] == [
        code for code, _ in ordered[:2]
    ]
    assert [cell.position for cell in result.step("largest").ranked] == [1, 2]
    # Two cells with the same value share a place, and both are kept.
    values = [Decimal(5)] * 63
    tied = store.write(altered(population(), values=values)).name
    result = run(
        store,
        {
            "name": "tied",
            "operation": "rank",
            "snapshot": tied,
            "selection": {"Kjonn": ["0"], "ContentsCode": ["Personer"], "Tid": ["2026"]},
            "by": "Alder",
            "order": "lowest",
            "count": 1,
        },
    )
    assert len(result.step("tied").ranked) == 7
    assert {cell.position for cell in result.step("tied").ranked} == {1}


def test_units_follow_the_operations(store: SnapshotStore) -> None:
    people = store.write(population()).name
    fish = store.write(catch()).name
    fish_cell = {
        "name": "fish",
        "operation": "value",
        "snapshot": fish,
        "codes": {
            "FiskeslagSamiske": "80a",
            "SamiskeElver": "18",
            "ContentsCode": "Fangst",
            "Tid": "2016",
        },
    }
    result = run(
        store,
        value("now", people, "2026"),
        value("before", people, "2025"),
        value("women", people, "2026", sex="2"),
        fish_cell,
        {
            "name": "share_now",
            "operation": "share_percent",
            "part": "women",
            "total": "now",
            "decimals": 2,
        },
        {
            "name": "share_before",
            "operation": "share_percent",
            "part": "women",
            "total": "before",
            "decimals": 2,
        },
        {"name": "points", "operation": "subtract", "left": "share_now", "right": "share_before"},
        {"name": "shares", "operation": "add", "left": "share_now", "right": "share_before"},
        {"name": "ratio", "operation": "divide", "left": "now", "right": "before", "decimals": 3},
        {
            "name": "per_person",
            "operation": "divide",
            "left": "fish",
            "right": "now",
            "decimals": 3,
        },
        {"name": "product", "operation": "multiply", "left": "fish", "right": "now"},
    )
    assert result.step("now").unit.model_dump() == {"kind": "amount", "name": "number", "scale": 0}
    assert result.step("share_now").unit.kind == "per cent"
    assert result.step("points").unit.kind == "percentage points"
    assert result.step("shares").unit.kind == "per cent"
    assert result.step("ratio").unit.kind == "ratio"
    assert result.step("per_person").unit.model_dump() == {
        "kind": "amount",
        "name": "kg / number",
        "scale": 0,
    }
    assert result.step("product").unit.name == "kg \u00d7 number"


def test_a_unit_named_per_cent_counts_as_per_cent(store: SnapshotStore) -> None:
    for name in ("prosent", "Per cent", "percent", "%"):
        units = [Unit(code="Personer", base=name, decimals=1)]
        snapshot = store.write(altered(population(), units=units)).name
        result = run(store, value("share", snapshot, "2026"))
        assert result.step("share").unit.kind == "per cent", name


MISSING: list[tuple[list[dict[str, Any]], str, str]] = [
    (
        [{"name": "a", "operation": "add", "left": "x", "right": "y"}],
        "a",
        "'x', which is not an earlier step",
    ),
    (
        [{"name": "a", "operation": "value", "snapshot": "0" * 64, "codes": {}}],
        "a",
        "cannot be read",
    ),
]


@pytest.mark.parametrize(("steps", "step", "message"), MISSING)
def test_a_plan_that_reads_what_does_not_exist_is_refused(
    store: SnapshotStore, steps: list[dict[str, Any]], step: str, message: str
) -> None:
    with pytest.raises(CalculationError, match=message) as caught:
        run(store, *steps)
    assert caught.value.step == step


def plan_error(store: SnapshotStore, *steps: dict[str, Any]) -> CalculationError:
    with pytest.raises(CalculationError) as caught:
        run(store, *steps)
    return caught.value


def test_names_and_inputs_are_checked_before_anything_runs(store: SnapshotStore) -> None:
    people = store.write(population()).name
    error = plan_error(store, value("a", people, "2026"), value("a", people, "2025"))
    assert (error.step, "same name" in str(error)) == ("a", True)
    rank = {
        "name": "top",
        "operation": "rank",
        "snapshot": people,
        "selection": {"Kjonn": ["0"], "ContentsCode": ["Personer"], "Tid": ["2026"]},
        "by": "Alder",
        "order": "highest",
        "count": 1,
    }
    error = plan_error(
        store, rank, {"name": "b", "operation": "add", "left": "top", "right": "top"}
    )
    assert error.step == "b"
    assert "a ranking with no single value" in str(error)


@pytest.mark.parametrize(
    ("codes", "message"),
    [
        ({"Kjonn": "0", "Alder": "999B", "ContentsCode": "Personer"}, "needs codes for Tid"),
        ({**TOTAL, "Tid": "2026", "Fylke": "03"}, "names Fylke, which the snapshot lacks"),
        ({**TOTAL, "Tid": "1999"}, "names 1999, which Tid lacks"),
    ],
)
def test_codes_are_checked_against_the_snapshot(
    store: SnapshotStore, codes: dict[str, str], message: str
) -> None:
    people = store.write(population()).name
    error = plan_error(
        store, {"name": "a", "operation": "value", "snapshot": people, "codes": codes}
    )
    assert error.step == "a"
    assert message in str(error)


def test_a_selection_may_not_repeat_a_code(store: SnapshotStore) -> None:
    people = store.write(population()).name
    selection = {
        "Kjonn": ["1", "1"],
        "Alder": ["999B"],
        "ContentsCode": ["Personer"],
        "Tid": ["2026"],
    }
    error = plan_error(
        store, {"name": "a", "operation": "sum", "snapshot": people, "selection": selection}
    )
    assert "twice" in str(error)


def test_a_step_selects_at_most_5000_cells(store: SnapshotStore) -> None:
    # 3 sexes x 600 ages x 3 years is 5,400 cells; 3 x 555 x 3 is 4,995.
    for codes, refused in ((600, True), (555, False)):
        snapshot = store.write(wide(codes)).name
        selection = {
            "Kjonn": ["0", "1", "2"],
            "Alder": [f"A{index}" for index in range(codes)],
            "ContentsCode": ["Personer"],
            "Tid": ["2024", "2025", "2026"],
        }
        step = {"name": "a", "operation": "sum", "snapshot": snapshot, "selection": selection}
        if refused:
            assert "more than the limit of 5,000" in str(plan_error(store, step))
        else:
            assert run(store, step).step("a").value == Decimal(3 * 3 * sum(range(codes)))


def test_a_ranking_fixes_every_other_dimension(store: SnapshotStore) -> None:
    people = store.write(population()).name
    rank = {
        "name": "top",
        "operation": "rank",
        "snapshot": people,
        "selection": {"Kjonn": ["0", "1"], "ContentsCode": ["Personer"], "Tid": ["2026"]},
        "by": "Alder",
        "order": "highest",
        "count": 3,
    }
    assert "must fix Kjonn to one code each" in str(plan_error(store, rank))


def test_reading_a_missing_value_fails_and_names_the_symbol(store: SnapshotStore) -> None:
    retrieval = symbols()
    [missing, *_] = [cell for cell in retrieval.result.observations if cell.value is None]
    snapshot = store.write(retrieval).name
    codes = dict(
        zip(
            [dimension.code for dimension in retrieval.result.dimensions],
            missing.codes,
            strict=True,
        )
    )
    error = plan_error(
        store, {"name": "gap", "operation": "value", "snapshot": snapshot, "codes": codes}
    )
    assert error.step == "gap"
    assert f"has the symbol {missing.symbol!r}" in str(error)


@pytest.mark.parametrize(
    "division",
    [
        {"name": "q", "operation": "divide", "left": "now", "right": "zero", "decimals": 1},
        {"name": "q", "operation": "change_percent", "old": "zero", "new": "now", "decimals": 1},
        {"name": "q", "operation": "share_percent", "part": "now", "total": "zero", "decimals": 1},
    ],
)
def test_dividing_by_zero_fails(store: SnapshotStore, division: dict[str, Any]) -> None:
    people = store.write(population()).name
    zero = {"name": "zero", "operation": "subtract", "left": "now", "right": "now"}
    error = plan_error(store, value("now", people, "2026"), zero, division)
    assert (error.step, "divides by zero" in str(error)) == ("q", True)


def test_a_result_of_more_than_38_digits_fails(store: SnapshotStore) -> None:
    people = store.write(population()).name
    now = recorded(POPULATION, "0", "999B", "Personer", "2026")
    steps = [
        value("now", people, "2026"),
        {"name": "p2", "operation": "multiply", "left": "now", "right": "now"},
        {"name": "p4", "operation": "multiply", "left": "p2", "right": "p2"},
        {"name": "p5", "operation": "multiply", "left": "p4", "right": "now"},
        {"name": "p6", "operation": "multiply", "left": "p5", "right": "now"},
    ]
    # Integers, since a Decimal power rounds to the context's 28 digits.
    digits = [len(str(int(now) ** power)) for power in (2, 4, 5, 6)]
    first_too_wide = next(
        name for name, count in zip(["p2", "p4", "p5", "p6"], digits, strict=True) if count > 38
    )
    error = plan_error(store, *steps)
    assert error.step == first_too_wide
    assert "more than 38" in str(error)


def test_mixing_units_fails(store: SnapshotStore) -> None:
    people = store.write(population()).name
    fish = store.write(catch()).name
    fish_cell = {
        "name": "fish",
        "operation": "value",
        "snapshot": fish,
        "codes": {
            "FiskeslagSamiske": "80a",
            "SamiskeElver": "18",
            "ContentsCode": "Fangst",
            "Tid": "2016",
        },
    }
    error = plan_error(
        store,
        value("now", people, "2026"),
        fish_cell,
        {"name": "sum", "operation": "add", "left": "now", "right": "fish"},
    )
    assert (error.step, "mixes units: number and kg" in str(error)) == ("sum", True)
    units = [
        Unit(code="Fangst", base="kg", decimals=0),
        Unit(code="Gjennomsnitt", base="tonn", decimals=1),
    ]
    mixed = store.write(altered(catch(), units=units)).name
    selection = {
        "FiskeslagSamiske": ["80a"],
        "SamiskeElver": ["18"],
        "ContentsCode": ["Fangst", "Gjennomsnitt"],
        "Tid": ["2016"],
    }
    error = plan_error(
        store, {"name": "both", "operation": "sum", "snapshot": mixed, "selection": selection}
    )
    assert (error.step, "mixes units: kg, tonn" in str(error)) == ("both", True)


def test_generated_sql_cannot_read_files_once_the_snapshots_are_loaded(
    store: SnapshotStore, tmp_path: Path
) -> None:
    people = store.write(population()).name
    outside = tmp_path / "outside.csv"
    outside.write_text("secret\n1\n", encoding="utf-8")
    connection, tables = locked({people: store.read(people)})
    with connection:
        assert connection.execute(f"SELECT count(*) FROM {tables[people]}").fetchone() == (63,)  # noqa: S608 - a generated table name
        for sql in (
            "SELECT * FROM read_csv(?)",
            "SELECT * FROM read_parquet(?)",
            "SELECT * FROM read_text(?)",
        ):
            with pytest.raises(duckdb.PermissionException):
                connection.execute(sql, [str(outside)])
        with pytest.raises(duckdb.Error):
            connection.execute("SET enable_external_access = true")


def test_a_floating_point_column_is_refused() -> None:
    with duckdb.connect() as connection:
        cursor = connection.execute("SELECT CAST(1.5 AS DOUBLE) AS share")
        with pytest.raises(CalculationError, match="share as DOUBLE, a binary number"):
            check_types(cursor, "s")


def floats(value: object) -> list[object]:
    if isinstance(value, float):
        return [value]
    if isinstance(value, dict):
        items = cast("dict[object, object]", value).values()
        return [found for item in items for found in floats(item)]
    if isinstance(value, list | tuple):
        return [found for item in cast("list[object]", value) for found in floats(item)]
    return []


def test_a_result_holds_no_float_and_names_its_snapshots(store: SnapshotStore) -> None:
    retrieval = population()
    people = store.write(retrieval).name
    result = run(
        store,
        value("now", people, "2026"),
        value("before", people, "2025"),
        {
            "name": "growth",
            "operation": "change_percent",
            "old": "before",
            "new": "now",
            "decimals": 2,
        },
    )
    assert floats(result.model_dump()) == []
    assert floats(json.loads(result.model_dump_json())) == []
    [use] = result.snapshots
    assert (use.snapshot_id, use.provenance) == (people, retrieval.result.provenance)


def test_the_readable_form_shows_the_sql_and_the_arithmetic(store: SnapshotStore) -> None:
    people = store.write(population()).name
    result = run(
        store,
        value("now", people, "2026"),
        value("before", people, "2025"),
        {
            "name": "growth",
            "operation": "change_percent",
            "old": "before",
            "new": "now",
            "decimals": 2,
        },
    )
    text = result.readable()
    assert result.step("now").sql[0] in text
    assert "with 0, 999B, Personer, 2026" in text
    growth = result.step("growth")
    assert f"rounded to {growth.value}" in text
    assert f"({result.step('now').value} - {result.step('before').value})" in text


def test_the_schema_names_every_operation() -> None:
    schema = json.dumps(plan_schema())
    for operation in (
        "value",
        "sum",
        "add",
        "subtract",
        "multiply",
        "divide",
        "mean",
        "change_percent",
        "share_percent",
        "rank",
    ):
        assert f'"{operation}"' in schema


AGES = {"Kjonn": ["0"], "ContentsCode": ["Personer"], "Tid": ["2026"]}


def rank(snapshot: str, order: str, count: int, **extra: Any) -> dict[str, Any]:
    step: dict[str, Any] = {
        "name": "ranked",
        "operation": "rank",
        "snapshot": snapshot,
        "selection": {code: list(codes) for code, codes in AGES.items()},
        "by": "Alder",
        "order": order,
        "count": count,
    }
    step.update(extra)
    return step


def test_later_steps_use_the_rounded_value(store: SnapshotStore) -> None:
    people = store.write(population()).name
    result = run(
        store,
        value("now", people, "2026"),
        value("before", people, "2025"),
        {
            "name": "growth",
            "operation": "change_percent",
            "old": "before",
            "new": "now",
            "decimals": 2,
        },
        {"name": "twice", "operation": "add", "left": "growth", "right": "growth"},
        {"name": "scaled", "operation": "multiply", "left": "growth", "right": "now"},
    )
    growth = result.step("growth")
    assert growth.value is not None
    assert Fraction(growth.exact or "0") != Fraction(growth.value)
    now = recorded(POPULATION, "0", "999B", "Personer", "2026")
    assert result.step("twice").value == growth.value + growth.value
    assert Fraction(result.step("scaled").value or 0) == Fraction(growth.value) * Fraction(now)


def test_the_exact_result_of_each_rounded_step_is_kept(store: SnapshotStore) -> None:
    people = store.write(population()).name
    years = {
        "Kjonn": ["0"],
        "Alder": ["999B"],
        "ContentsCode": ["Personer"],
        "Tid": ["2024", "2025", "2026"],
    }
    result = run(
        store,
        value("now", people, "2026"),
        value("before", people, "2025"),
        value("women", people, "2026", sex="2"),
        {
            "name": "average",
            "operation": "mean",
            "snapshot": people,
            "selection": years,
            "decimals": 0,
        },
        {"name": "ratio", "operation": "divide", "left": "now", "right": "before", "decimals": 3},
        {
            "name": "share",
            "operation": "share_percent",
            "part": "women",
            "total": "now",
            "decimals": 1,
        },
    )
    now = Fraction(recorded(POPULATION, "0", "999B", "Personer", "2026"))
    before = Fraction(recorded(POPULATION, "0", "999B", "Personer", "2025"))
    women = Fraction(recorded(POPULATION, "2", "999B", "Personer", "2026"))
    total = sum(
        (
            Fraction(recorded(POPULATION, "0", "999B", "Personer", year))
            for year in ("2024", "2025", "2026")
        ),
        Fraction(0),
    )
    for name, exact in (
        ("average", total / 3),
        ("ratio", now / before),
        ("share", women / now * 100),
    ):
        written = (
            str(exact.numerator)
            if exact.denominator == 1
            else f"{exact.numerator}/{exact.denominator}"
        )
        assert result.step(name).exact == written, name


def test_a_difference_keeps_its_unit_and_of_percentage_points_too(store: SnapshotStore) -> None:
    people = store.write(population()).name
    result = run(
        store,
        value("now", people, "2026"),
        value("before", people, "2025"),
        value("women", people, "2026", sex="2"),
        {"name": "more", "operation": "subtract", "left": "now", "right": "before"},
        {
            "name": "share_now",
            "operation": "share_percent",
            "part": "women",
            "total": "now",
            "decimals": 2,
        },
        {
            "name": "share_before",
            "operation": "share_percent",
            "part": "women",
            "total": "before",
            "decimals": 2,
        },
        {"name": "points", "operation": "subtract", "left": "share_now", "right": "share_before"},
        {"name": "again", "operation": "subtract", "left": "points", "right": "points"},
    )
    assert result.step("more").unit.model_dump() == {"kind": "amount", "name": "number", "scale": 0}
    assert result.step("points").unit.kind == "percentage points"
    assert result.step("again").unit.kind == "percentage points"


def test_a_unit_named_percentage_points_counts_as_such(store: SnapshotStore) -> None:
    # A unit that no recorded table has.
    for name in ("prosentpoeng", "Percentage points"):
        units = [Unit(code="Personer", base=name, decimals=1)]
        snapshot = store.write(altered(population(), units=units)).name
        assert (
            run(store, value("gap", snapshot, "2026")).step("gap").unit.kind == "percentage points"
        )


def test_a_ranking_of_the_lowest_follows_the_recorded_values(store: SnapshotStore) -> None:
    people = store.write(population()).name
    result = run(store, rank(people, "lowest", 3))
    ages = sorted(
        (
            (Decimal(token), cell[1])
            for cell, token, _ in recorded_cells(POPULATION)
            if cell[0] == "0" and cell[3] == "2026"
        ),
    )
    assert [cell.codes[1] for cell in result.step("ranked").ranked] == [
        code for _, code in ages[:3]
    ]
    assert [cell.value for cell in result.step("ranked").ranked] == [
        number for number, _ in ages[:3]
    ]


def test_a_ranking_counts_places_as_rank_does(store: SnapshotStore) -> None:
    # Two ages tie for the top, a third follows: the top two places are both first, so a
    # count of 2 gives the two and not the third. These values stand for no response.
    base = population()
    values: list[Decimal | None] = []
    for cell in base.result.observations:
        if cell.codes[1] in ("00-06", "07-15"):
            values.append(Decimal(9))
        elif cell.codes[1] == "16-44":
            values.append(Decimal(8))
        else:
            values.append(Decimal(1))
    tied = store.write(altered(base, values=values)).name
    result = run(store, rank(tied, "highest", 2))
    assert [cell.codes[1] for cell in result.step("ranked").ranked] == ["00-06", "07-15"]
    assert [cell.position for cell in result.step("ranked").ranked] == [1, 1]
    result = run(store, rank(tied, "highest", 3))
    assert [cell.position for cell in result.step("ranked").ranked] == [1, 1, 3]


def test_a_ranking_may_list_the_codes_it_ranks(store: SnapshotStore) -> None:
    people = store.write(population()).name
    chosen = ["00-06", "07-15", "80+"]
    step = rank(people, "highest", 10)
    step["selection"]["Alder"] = chosen
    result = run(store, step)
    assert sorted(cell.codes[1] for cell in result.step("ranked").ranked) == sorted(chosen)


def test_a_ranking_by_an_unknown_dimension_is_refused(store: SnapshotStore) -> None:
    people = store.write(population()).name
    error = plan_error(store, rank(people, "highest", 1, by="Fylke"))
    assert (error.step, "ranks by 'Fylke'" in str(error)) == ("ranked", True)


def test_a_ranking_by_a_dimension_called_position_works(store: SnapshotStore) -> None:
    # A dimension code that no recorded table has, which the ranking SQL must not mistake
    # for the place it computes.
    base = population()
    dimensions = list(base.result.dimensions)
    dimensions[1] = dimensions[1].model_copy(update={"code": "position"})
    snapshot = store.write(altered(base, dimensions=dimensions)).name
    step = rank(snapshot, "highest", 2, by="position")
    result = run(store, step)
    assert [cell.position for cell in result.step("ranked").ranked] == [1, 2]


def test_a_selection_without_codes_is_refused(store: SnapshotStore) -> None:
    people = store.write(population()).name
    selection = {"Kjonn": [], "Alder": ["999B"], "ContentsCode": ["Personer"], "Tid": ["2026"]}
    error = plan_error(
        store, {"name": "a", "operation": "sum", "snapshot": people, "selection": selection}
    )
    assert "selects no code in Kjonn" in str(error)


def test_exactly_5000_cells_are_allowed(store: SnapshotStore) -> None:
    # 2 sexes x 1,250 ages x 2 years is 5,000 cells, and 1,251 ages give 5,004.
    for codes, allowed in ((1250, True), (1251, False)):
        snapshot = store.write(wide(codes)).name
        selection = {
            "Kjonn": ["1", "2"],
            "Alder": [f"A{index}" for index in range(codes)],
            "ContentsCode": ["Personer"],
            "Tid": ["2025", "2026"],
        }
        step = {"name": "a", "operation": "sum", "snapshot": snapshot, "selection": selection}
        if allowed:
            assert run(store, step).step("a").value == Decimal(2 * 2 * sum(range(codes)))
        else:
            assert "more than the limit" in str(plan_error(store, step))


@pytest.mark.parametrize(
    ("step", "allowed"),
    [
        ({"decimals": 20}, True),
        ({"decimals": 21}, False),
        ({"decimals": -1}, False),
    ],
)
def test_the_decimals_are_bounded(step: dict[str, Any], allowed: bool) -> None:
    plan = {"steps": [{"name": "q", "operation": "divide", "left": "a", "right": "b", **step}]}
    if allowed:
        assert Plan.model_validate_json(json.dumps(plan))
    else:
        with pytest.raises(ValidationError):
            Plan.model_validate_json(json.dumps(plan))


@pytest.mark.parametrize(("count", "allowed"), [(1, True), (10, True), (0, False), (11, False)])
def test_a_ranking_returns_1_to_10_cells(count: int, allowed: bool) -> None:
    plan = {"steps": [rank("0" * 64, "highest", count)]}
    if allowed:
        assert Plan.model_validate_json(json.dumps(plan))
    else:
        with pytest.raises(ValidationError):
            Plan.model_validate_json(json.dumps(plan))


@pytest.mark.parametrize(("steps", "allowed"), [(20, True), (21, False), (0, False)])
def test_a_plan_has_1_to_20_steps(steps: int, allowed: bool) -> None:
    plan = {"steps": [value(f"s{index}", "0" * 64, "2026") for index in range(steps)]}
    if allowed:
        assert Plan.model_validate_json(json.dumps(plan))
    else:
        with pytest.raises(ValidationError):
            Plan.model_validate_json(json.dumps(plan))


def test_a_plan_that_repeats_a_key_is_refused() -> None:
    text = (
        '{"steps": [{"name": "a", "operation": "value", "snapshot": "%s", "codes": {"Tid": "2025", "Tid": "2026"}}]}'
        % ("0" * 64)
    )
    with pytest.raises(ValueError, match="names Tid more than once"):
        parse_plan(text)
    assert parse_plan(text.replace('"Tid": "2025", ', "")).steps[0].name == "a"


def test_mixing_units_fails_in_a_mean_and_a_ranking(store: SnapshotStore) -> None:
    # Units that no recorded table has: tonnes beside kilograms in one table.
    units = [
        Unit(code="Fangst", base="kg", decimals=0),
        Unit(code="Gjennomsnitt", base="tonn", decimals=1),
    ]
    mixed = store.write(altered(catch(), units=units)).name
    selection = {
        "FiskeslagSamiske": ["80a"],
        "SamiskeElver": ["18"],
        "ContentsCode": ["Fangst", "Gjennomsnitt"],
        "Tid": ["2016"],
    }
    error = plan_error(
        store,
        {
            "name": "m",
            "operation": "mean",
            "snapshot": mixed,
            "selection": selection,
            "decimals": 1,
        },
    )
    assert (error.step, "mixes units" in str(error)) == ("m", True)
    ranking = {
        "name": "r",
        "operation": "rank",
        "snapshot": mixed,
        "selection": {"FiskeslagSamiske": ["80a"], "SamiskeElver": ["18"], "Tid": ["2016"]},
        "by": "ContentsCode",
        "order": "highest",
        "count": 2,
    }
    error = plan_error(store, ranking)
    assert (error.step, "mixes units" in str(error)) == ("r", True)


def test_results_of_38_digits_pass_and_of_39_fail(store: SnapshotStore) -> None:
    # Values of 38 digits, which no recorded table has.
    nines = Decimal("9" * 38)
    base = population()
    values = [nines if cell.codes[3] == "2026" else Decimal(1) for cell in base.result.observations]
    snapshot = store.write(altered(base, values=values)).name
    one = {
        "name": "one",
        "operation": "value",
        "snapshot": snapshot,
        "codes": {**TOTAL, "Tid": "2025"},
    }
    result = run(
        store,
        value("big", snapshot, "2026"),
        one,
        {"name": "less", "operation": "subtract", "left": "big", "right": "one"},
    )
    # Written out, since Decimal arithmetic rounds to the context's 28 digits.
    assert result.step("less").value == Decimal("9" * 37 + "8")
    error = plan_error(
        store,
        value("big", snapshot, "2026"),
        one,
        {"name": "more", "operation": "add", "left": "big", "right": "one"},
    )
    assert (error.step, "needs 39 digits" in str(error)) == ("more", True)


def test_a_tiny_result_fails_with_the_step_named(store: SnapshotStore) -> None:
    # 0.1 squared again and again passes 38 decimals; a value that no recording holds.
    snapshot = store.write(altered(population(), values=[Decimal("0.1")] * 63)).name
    steps: list[dict[str, Any]] = [value("p1", snapshot, "2026")]
    for index in range(1, 8):
        steps.append(
            {
                "name": f"p{index + 1}",
                "operation": "multiply",
                "left": f"p{index}",
                "right": f"p{index}",
            }
        )
    error = plan_error(store, *steps)
    # 0.1 to the power 2**6 = 64 has 64 decimals; to the power 2**5, 32.
    assert error.step == "p7"
    assert "more than 38" in str(error)


def test_numbers_are_written_in_fixed_point_and_as_ssb_wrote_them(store: SnapshotStore) -> None:
    small = store.write(altered(population(), values=[Decimal("0.0000001")] * 63)).name
    result = run(
        store,
        value("tiny", small, "2026"),
        {"name": "sum", "operation": "add", "left": "tiny", "right": "tiny"},
    )
    text = result.readable()
    assert "E-" not in text
    assert "tiny = 0.0000001" in text
    assert "= 0.0000002" in text
    # Table 06743 has a scale of 1, but SSB wrote its catches without decimals.
    fish = store.write(catch()).name
    codes = {
        "FiskeslagSamiske": "80a",
        "SamiskeElver": "18",
        "ContentsCode": "Fangst",
        "Tid": "2016",
    }
    token = str(recorded("data-06743-decimals-no", "80a", "18", "Fangst", "2016"))
    result = run(store, {"name": "catch", "operation": "value", "snapshot": fish, "codes": codes})
    assert f"  catch = {token}" in result.readable().splitlines()


def with_float_values(store: SnapshotStore, snapshot: str) -> None:
    """Rewrite a snapshot's cells with the value as DOUBLE, as a foreign file might hold."""
    directory = store.path(snapshot)
    parquet = directory / OBSERVATIONS
    copy = directory / "double.parquet"
    with duckdb.connect() as connection:
        connection.execute(
            "CREATE TABLE cells AS SELECT * REPLACE (CAST(value AS DOUBLE) AS value, "
            "CAST(value_text AS DOUBLE) AS value_text) FROM read_parquet(?)",
            [str(parquet)],
        )
        connection.execute(f"COPY cells TO '{copy}' (FORMAT parquet)")
    copy.replace(parquet)
    data = json.loads((directory / MANIFEST).read_text(encoding="utf-8"))
    data["observations_sha256"] = hashlib.sha256(parquet.read_bytes()).hexdigest()
    (directory / MANIFEST).write_text(json.dumps(data), encoding="utf-8")


def test_a_snapshot_with_floating_point_values_is_refused_before_any_step(
    store: SnapshotStore,
) -> None:
    # Reading the snapshot refuses the binary numbers, so the engine's own check of the
    # column types, tested above, guards SQL that a valid snapshot never produces.
    people = store.write(population()).name
    with_float_values(store, people)
    for step in (value("cell", people, "2026"), rank(people, "highest", 2)):
        error = plan_error(store, step)
        assert error.step == step["name"]
        assert "cannot be read" in str(error)


@pytest.mark.parametrize(
    ("name", "scale"),
    [
        ("1000 personer", 3),
        ("1 000 persons", 3),
        ("1 000 kr", 3),
        ("1000 man-weeks", 3),
        ("10 000 tonn", 4),
        ("mill. kr", 6),
        ("NOK million", 6),
        ("mrd. kr", 9),
        ("tusen kroner", 3),
        ("NOK 1000", 3),
        ("NOK 1 000", 3),
        ("Antall (1000)", 3),
        ("mill.kr", 6),
        ("NOK millions", 6),
        ("thousands of persons", 3),
        ("mill. kr per innbygger", 6),
        ("1000 kr/år", 3),
        ("Personer, 1000, sesongjustert", 3),
        ("Kr, 1 000.", 3),
        ("1 000 000 kr", 6),
        # A rate, and units that name no multiplier.
        ("per 1000", 0),
        ("per million", 0),
        ("Fødte per tusen innbyggere", 0),
        ("Døde pr. 1000 fødte", 0),
        ("Antall/1000 innbyggere", 0),
        ("Indeks (1998 = 1000)", 0),
        ("Poeng (0-1000)", 0),
        ("Poeng (0 - 1000)", 0),
        ("Poeng (0 \u2013 1000)", 0),
        ("1000,5 kr", 0),
        # A power that no result unit can hold is no multiplier.
        ("1" + "0" * 31 + " kr", 0),
        ("indeks (2015=100)", 0),
        ("indeks (1998=1000)", 0),
        ("100=2015", 0),
        ("100 personer", 0),
        ("12 måneder", 0),
        ("Lovbrudd per 1000 innbyggere", 0),
        ("personer", 0),
        ("kg", 0),
        ("antall per km²", 0),
        ("2 personer", 0),
        (None, 0),
    ],
)
def test_a_unit_s_name_gives_its_power_of_ten(name: str | None, scale: int) -> None:
    assert unit_scale(name) == scale


def test_the_scale_of_a_unit_follows_the_operations(store: SnapshotStore) -> None:
    units = [Unit(code="Personer", base="1000 personer", decimals=0)]
    snapshot = store.write(altered(population(), units=units)).name
    result = run(
        store,
        value("now", snapshot, "2026"),
        value("before", snapshot, "2025"),
        {"name": "more", "operation": "subtract", "left": "now", "right": "before"},
        {"name": "squared", "operation": "multiply", "left": "now", "right": "before"},
        {"name": "ratio", "operation": "divide", "left": "now", "right": "before", "decimals": 3},
        {
            "name": "growth",
            "operation": "change_percent",
            "old": "before",
            "new": "now",
            "decimals": 1,
        },
    )

    assert result.step("now").unit.scale == 3
    assert result.step("more").unit.scale == 3
    assert result.step("squared").unit.scale == 6
    assert result.step("ratio").unit.model_dump() == {"kind": "ratio", "name": None, "scale": 0}
    assert result.step("growth").unit.scale == 0


def test_a_quotient_of_two_units_subtracts_their_scales(store: SnapshotStore) -> None:
    units = [Unit(code="Personer", base="1000 personer", decimals=0)]
    people = store.write(altered(population(), units=units)).name
    fish = store.write(catch()).name
    codes = {
        "FiskeslagSamiske": "80a",
        "SamiskeElver": "18",
        "ContentsCode": "Fangst",
        "Tid": "2016",
    }
    result = run(
        store,
        value("now", people, "2026"),
        {"name": "fish", "operation": "value", "snapshot": fish, "codes": codes},
        {
            "name": "per_person",
            "operation": "divide",
            "left": "fish",
            "right": "now",
            "decimals": 9,
        },
        {
            "name": "persons_per_kg",
            "operation": "divide",
            "left": "now",
            "right": "fish",
            "decimals": 3,
        },
    )

    # kg per 1000 personer is a thousandth of kg per person, and the other way round.
    assert result.step("per_person").unit.scale == -3
    assert result.step("persons_per_kg").unit.scale == 3


def test_a_per_cent_unit_has_no_multiplier_whatever_its_name_holds() -> None:
    assert unit_named("Prosent av BNP, mill.").model_dump() == {
        "kind": "per cent",
        "name": "Prosent av BNP, mill.",
        "scale": 0,
    }


def test_a_per_cent_of_two_scales_is_taken_in_the_base_unit(store: SnapshotStore) -> None:
    # The same cell, once in 1000 personer and once in persons: the first is a thousand
    # times the second.
    units = [Unit(code="Personer", base="1000 personer", decimals=0)]
    thousands = store.write(altered(population(), units=units)).name
    persons = store.write(population()).name
    result = run(
        store,
        value("big", thousands, "2026"),
        value("small", persons, "2026"),
        {
            "name": "share",
            "operation": "share_percent",
            "part": "big",
            "total": "small",
            "decimals": 0,
        },
        {
            "name": "growth",
            "operation": "change_percent",
            "old": "small",
            "new": "big",
            "decimals": 0,
        },
    )

    assert result.step("share").exact == "100000"
    assert result.step("growth").exact == "99900"


def test_a_per_cent_of_a_negative_scale_is_exact(store: SnapshotStore) -> None:
    # persons / 1000 personer has the scale -3: the quotient 1 is a thousandth.
    units = [Unit(code="Personer", base="1000 personer", decimals=0)]
    thousands = store.write(altered(population(), units=units)).name
    persons = store.write(population()).name
    result = run(
        store,
        value("big", thousands, "2026"),
        value("small", persons, "2026"),
        {"name": "quotient", "operation": "divide", "left": "small", "right": "big", "decimals": 3},
        {
            "name": "growth",
            "operation": "change_percent",
            "old": "quotient",
            "new": "small",
            "decimals": 0,
        },
    )
    small = Fraction(result.step("small").exact or "0")

    assert result.step("quotient").unit.scale == -3
    assert result.step("growth").exact == str((1000 * small - 1) * 100)


# Pieces of the statement itself, so that a dimension code built from them would
# mislead a reader that searched the statement. No SSB code holds them.
PIECES = ['"', " IN (?", ", ?", ")", " AND ", " WHERE ", " ORDER BY ", "snapshot_1", "x", "1"]


@st.composite
def statements(draw: st.DrawFn) -> tuple[str, list[str], list[str], tuple[tuple[str, str], ...]]:
    """A statement that the engine writes, its parameters, dimensions and bound codes."""
    code = st.lists(st.sampled_from(PIECES), min_size=1, max_size=4).map("".join)
    dimensions = draw(st.lists(code, min_size=1, max_size=4, unique=True))
    counts = [draw(st.integers(1, 4)) for _ in dimensions]
    parameters = [f"{draw(st.sampled_from(PIECES))}{index}" for index in range(sum(counts))]
    bound: list[tuple[str, str]] = []
    for dimension, count in zip(dimensions, counts, strict=True):
        bound += [(dimension, parameters[len(bound) + place]) for place in range(count)]
    table = f"snapshot_{draw(st.integers(1, 20))}"
    return select_statement(table, dimensions, counts), parameters, dimensions, tuple(bound)


@given(statements())
def test_a_statement_that_the_engine_writes_reads_back_as_its_codes(
    statement: tuple[str, list[str], list[str], tuple[tuple[str, str], ...]],
) -> None:
    sql, parameters, dimensions, bound = statement

    assert bound_codes(sql, parameters, dimensions) == bound
    # With a parameter too few or too many, or the dimensions in another order, the
    # statement is not one that the engine wrote for them.
    assert bound_codes(sql, parameters[:-1], dimensions) is None
    assert bound_codes(sql, [*parameters, "x"], dimensions) is None
    if len(dimensions) > 1:
        assert bound_codes(sql, parameters, dimensions[::-1]) is None


STATEMENT = select_statement("snapshot_1", ["Kjonn", "Tid"], [2, 1])


@pytest.mark.parametrize(
    "sql",
    [
        STATEMENT.replace(" AND ", " OR "),
        STATEMENT.replace(" ORDER BY", " AND 1 = 1 ORDER BY"),
        f"{STATEMENT} LIMIT 1",
        STATEMENT.replace("snapshot_1", "snapshot_0"),
        STATEMENT.replace("snapshot_1", "cells"),
        STATEMENT.replace('"Tid" IN (?)', '"Tid" = ?'),
        STATEMENT.replace('"Kjonn" IN', '"Kjonn" NOT IN'),
        STATEMENT.replace("(?, ?)", "(?,?)"),
        STATEMENT.replace("SELECT", "select"),
    ],
)
def test_a_statement_in_another_form_reads_as_no_codes(sql: str) -> None:
    assert bound_codes(STATEMENT, ["1", "2", "2026"], ["Kjonn", "Tid"]) == (
        ("Kjonn", "1"),
        ("Kjonn", "2"),
        ("Tid", "2026"),
    )
    assert bound_codes(sql, ["1", "2", "2026"], ["Kjonn", "Tid"]) is None


def test_each_step_s_statement_reads_back_as_the_cells_it_selected(store: SnapshotStore) -> None:
    people = store.write(population()).name
    dimensions = [dimension.code for dimension in store.read(people).manifest.dimensions]
    both = {"Kjonn": ["1", "2"], "Alder": ["999B"], "ContentsCode": ["Personer"], "Tid": ["2026"]}
    years = {**both, "Kjonn": ["0"], "Tid": ["2024", "2025", "2026"]}
    result = run(
        store,
        value("now", people, "2026"),
        {"name": "sexes", "operation": "sum", "snapshot": people, "selection": both},
        {
            "name": "average",
            "operation": "mean",
            "snapshot": people,
            "selection": years,
            "decimals": 0,
        },
        {
            "name": "top",
            "operation": "rank",
            "snapshot": people,
            "selection": both,
            "by": "Kjonn",
            "order": "highest",
            "count": 1,
        },
    )

    def read(name: str, statement: int = 0) -> tuple[tuple[str, str], ...] | None:
        step = result.step(name)
        return bound_codes(step.sql[statement], step.parameters[statement], dimensions)

    def cells(selection: dict[str, list[str]]) -> tuple[tuple[str, str], ...]:
        return tuple((code, item) for code in dimensions for item in selection[code])

    assert read("now") == cells({**TOTAL_CODES, "Tid": ["2026"]})
    assert read("sexes") == cells(both)
    assert read("average") == cells(years)
    assert read("top") == cells(both)
    # The ranking statement adds a window and a limit, so it is in another form.
    assert read("top", 1) is None

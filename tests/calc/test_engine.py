"""The calculation engine over snapshots of recorded responses.

Expected numbers come from the recordings in `tests/fixtures/ssb/`, computed here with
`Fraction`, never written by hand.
"""

from __future__ import annotations

import json
import math
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any, cast

import duckdb
import pytest
from recorded import recorded_cells
from snapshotting import altered, catch, population, symbols, wide

from opencited.calc import CalculationError, CalculationResult, Engine, Plan, plan_schema
from opencited.calc.engine import check_types, locked
from opencited.snapshots import SnapshotStore
from opencited.sources import Unit

POPULATION = "data-05810-expressions-en"
TOTAL = {"Kjonn": "0", "Alder": "999B", "ContentsCode": "Personer"}


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
    assert result.step("now").unit.model_dump() == {"kind": "amount", "name": "number"}
    assert result.step("share_now").unit.kind == "per cent"
    assert result.step("points").unit.kind == "percentage points"
    assert result.step("shares").unit.kind == "per cent"
    assert result.step("ratio").unit.kind == "ratio"
    assert result.step("per_person").unit.model_dump() == {"kind": "amount", "name": "kg / number"}
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
    connection, tables = locked(store, [people])
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
    assert math.isfinite(len(schema))

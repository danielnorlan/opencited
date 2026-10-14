"""The calculation engine: checks a plan, selects cells with DuckDB and computes exactly.

The engine checks the whole plan before it runs any step. It then loads the snapshots
that the plan names into an in-memory DuckDB database, turns off external access and
locks the configuration, and only then runs the SQL it generates, with every code bound
as a parameter. DuckDB selects and ranks cells; all arithmetic runs on exact rational
numbers in ``opencited.calc.arithmetic`` (ADR 0011).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from typing import cast

import duckdb

from opencited.calc import arithmetic
from opencited.calc.plan import (
    BinaryStep,
    ChangePercentStep,
    DivideStep,
    MeanStep,
    Plan,
    RankStep,
    SharePercentStep,
    Step,
    SumStep,
    ValueStep,
    inputs,
)
from opencited.calc.result import (
    CalculationResult,
    RankedCell,
    ResultUnit,
    SnapshotUse,
    StepResult,
)
from opencited.snapshots import OBSERVATIONS, Snapshot, SnapshotError, SnapshotStore, quoted

# A step selects at most this many cells, as a query does (spec 0002).
MAX_CELLS = 5000
# A unit counts as per cent when its name is one of these, as specs 0002 and 0003 write
# them, in any case.
PER_CENT = frozenset({"prosent", "per cent", "percent", "%"})
FLOATING = frozenset({"DOUBLE", "FLOAT", "REAL"})
# An arithmetic line lists the cells of a sum or a mean when there are at most this many.
LISTED = 10
# The multiplication sign in the readable arithmetic.
TIMES = "\u00d7"


class CalculationError(Exception):
    """A plan that cannot run, or a step that fails. ``step`` names the step, if any."""

    def __init__(self, message: str, step: str | None = None) -> None:
        super().__init__(message if step is None else f"Step {step!r} {message}")
        self.step = step


@dataclass(frozen=True)
class Outcome:
    """The result of a numeric step, which later steps read."""

    value: Decimal
    unit: ResultUnit


@dataclass(frozen=True)
class Row:
    codes: tuple[str, ...]
    value: Decimal | None
    symbol: str | None
    unit: str | None


def unit_named(name: str | None) -> ResultUnit:
    """Return the unit of a cell, which is per cent when its name says so."""
    if name is not None and name.strip().lower() in PER_CENT:
        return ResultUnit(kind="per cent", name=name)
    return ResultUnit(kind="amount", name=name)


def same_unit(left: ResultUnit, right: ResultUnit) -> bool:
    if left.kind != right.kind:
        return False
    return left.kind != "amount" or left.name == right.name


class Engine:
    """Runs calculation plans over the snapshots in one store."""

    def __init__(self, store: SnapshotStore) -> None:
        self.store = store

    def run(self, plan: Plan) -> CalculationResult:
        """Check a plan, run every step and return the steps with their results."""
        snapshots = self._check(plan)
        connection, tables = locked(self.store, list(snapshots))
        with connection:
            runner = Runner(connection, snapshots, tables)
            steps = tuple(runner.step(step) for step in plan.steps)
        uses = tuple(
            SnapshotUse(snapshot_id=snapshot_id, provenance=snapshot.manifest.provenance)
            for snapshot_id, snapshot in snapshots.items()
        )
        return CalculationResult(steps=steps, snapshots=uses)

    def _check(self, plan: Plan) -> dict[str, Snapshot]:
        """Check a plan before it runs, and return the snapshots it reads, in order."""
        numeric: set[str] = set()
        seen: set[str] = set()
        snapshots: dict[str, Snapshot] = {}
        for step in plan.steps:
            if step.name in seen:
                raise CalculationError("has the same name as an earlier step", step.name)
            for name in inputs(step):
                if name not in seen:
                    raise CalculationError(
                        f"reads {name!r}, which is not an earlier step", step.name
                    )
                if name not in numeric:
                    raise CalculationError(
                        f"reads {name!r}, a ranking with no single value", step.name
                    )
            seen.add(step.name)
            if not isinstance(step, RankStep):
                numeric.add(step.name)
            snapshot_id = getattr(step, "snapshot", None)
            if isinstance(snapshot_id, str):
                if snapshot_id not in snapshots:
                    try:
                        snapshots[snapshot_id] = self.store.read(snapshot_id)
                    except SnapshotError as error:
                        raise CalculationError(
                            f"reads snapshot {snapshot_id}, which cannot be read: {error}",
                            step.name,
                        ) from error
                check_selection(step, snapshots[snapshot_id])
        return snapshots


def locked(
    store: SnapshotStore, snapshot_ids: Sequence[str]
) -> tuple[duckdb.DuckDBPyConnection, dict[str, str]]:
    """Return an in-memory database with the snapshots loaded, and the table of each.

    The database can read no file and change none of its settings afterwards, so the
    SQL that the engine generates can only read the loaded tables.
    """
    tables = {
        snapshot_id: f"snapshot_{index}" for index, snapshot_id in enumerate(snapshot_ids, start=1)
    }
    connection = duckdb.connect(":memory:")
    try:
        for snapshot_id, table in tables.items():
            path = store.path(snapshot_id) / OBSERVATIONS
            connection.execute(
                f"CREATE TABLE {table} AS SELECT * FROM read_parquet(?)",  # noqa: S608 - a generated table name only
                [str(path)],
            )
        connection.execute("SET enable_external_access = false")
        connection.execute("SET lock_configuration = true")
    except BaseException:
        connection.close()
        raise
    return connection, tables


def selection_of(step: Step) -> dict[str, tuple[str, ...]]:
    match step:
        case ValueStep():
            return {code: (value,) for code, value in step.codes.items()}
        case SumStep() | MeanStep() | RankStep():
            return dict(step.selection)
        case _:
            return {}


def check_selection(step: Step, snapshot: Snapshot) -> None:
    """Refuse a selection that misses a dimension, names an unknown code or is too large."""
    selection = selection_of(step)
    dimensions = {dimension.code: dimension for dimension in snapshot.manifest.dimensions}
    unknown = sorted(set(selection) - set(dimensions))
    if unknown:
        raise CalculationError(f"names {', '.join(unknown)}, which the snapshot lacks", step.name)
    if isinstance(step, RankStep):
        if step.by not in dimensions:
            raise CalculationError(f"ranks by {step.by!r}, which the snapshot lacks", step.name)
        if step.by not in selection:
            selection[step.by] = tuple(value.code for value in dimensions[step.by].values)
        loose = [code for code, codes in selection.items() if code != step.by and len(codes) != 1]
        if loose:
            raise CalculationError(
                f"must fix {', '.join(loose)} to one code each, since it ranks by {step.by}",
                step.name,
            )
    missing = [code for code in dimensions if code not in selection]
    if missing:
        raise CalculationError(f"needs codes for {', '.join(missing)}", step.name)
    for code, codes in selection.items():
        known = {value.code for value in dimensions[code].values}
        if not codes:
            raise CalculationError(f"selects no code in {code}", step.name)
        if len(set(codes)) != len(codes):
            raise CalculationError(f"lists a code of {code} twice", step.name)
        absent = [value for value in codes if value not in known]
        if absent:
            raise CalculationError(f"names {', '.join(absent)}, which {code} lacks", step.name)
    cells = math.prod(len(codes) for codes in selection.values())
    if cells > MAX_CELLS:
        raise CalculationError(
            f"selects {cells:,} cells, more than the limit of {MAX_CELLS:,}", step.name
        )


class Runner:
    """Runs the steps of a checked plan against a locked DuckDB connection."""

    def __init__(
        self,
        connection: duckdb.DuckDBPyConnection,
        snapshots: dict[str, Snapshot],
        tables: dict[str, str],
    ) -> None:
        self.connection = connection
        self.snapshots = snapshots
        self.tables = tables
        self.outcomes: dict[str, Outcome] = {}

    def step(self, step: Step) -> StepResult:
        match step:
            case ValueStep() | SumStep() | MeanStep():
                result = self.cells(step)
            case RankStep():
                result = self.rank(step)
            case BinaryStep() | DivideStep() | ChangePercentStep() | SharePercentStep():
                result = self.combine(step)
        if result.value is not None:
            digits = arithmetic.significant(result.value)
            if digits > arithmetic.MAX_SIGNIFICANT:
                raise CalculationError(
                    f"gives a result with {digits} significant digits, more than "
                    f"{arithmetic.MAX_SIGNIFICANT}",
                    step.name,
                )
            self.outcomes[step.name] = Outcome(result.value, result.unit)
        return result

    def select(
        self, step: ValueStep | SumStep | MeanStep | RankStep
    ) -> tuple[str, tuple[str, ...], list[Row]]:
        """Return the SQL, its parameters and the cells that a step selects."""
        snapshot = self.snapshots[step.snapshot]
        selection = selection_of(step)
        codes = [dimension.code for dimension in snapshot.manifest.dimensions]
        if isinstance(step, RankStep) and step.by not in selection:
            dimension = next(item for item in snapshot.manifest.dimensions if item.code == step.by)
            selection[step.by] = tuple(value.code for value in dimension.values)
        clauses: list[str] = []
        parameters: list[str] = []
        for code in codes:
            chosen = selection[code]
            clauses.append(f"{quoted(code)} IN ({', '.join('?' * len(chosen))})")
            parameters.extend(chosen)
        columns = ", ".join(quoted(code) for code in codes)
        sql = (
            f"SELECT {columns}, value, symbol, unit FROM {self.tables[step.snapshot]} "  # noqa: S608 - quoted names, bound codes
            f"WHERE {' AND '.join(clauses)} ORDER BY {columns}"
        )
        cursor = self.connection.execute(sql, parameters)
        check_types(cursor, step.name)
        count = len(codes)
        rows = [
            Row(
                codes=tuple(cast("Sequence[str]", row[:count])),
                value=cast("Decimal | None", row[count]),
                symbol=cast("str | None", row[count + 1]),
                unit=cast("str | None", row[count + 2]),
            )
            for row in cursor.fetchall()
        ]
        expected = math.prod(len(chosen) for chosen in selection.values())
        if len(rows) != expected:
            raise CalculationError(
                f"selected {len(rows)} cells where the snapshot should have {expected}", step.name
            )
        for row in rows:
            if row.value is None:
                raise CalculationError(
                    f"reads a missing value: the cell {', '.join(row.codes)} has the symbol "
                    f"{row.symbol!r}",
                    step.name,
                )
        units = sorted({row.unit or "" for row in rows})
        if len(units) > 1:
            raise CalculationError(f"mixes units: {', '.join(units)}", step.name)
        return sql, tuple(parameters), rows

    def cells(self, step: ValueStep | SumStep | MeanStep) -> StepResult:
        sql, parameters, rows = self.select(step)
        values = [cast("Decimal", row.value) for row in rows]
        unit = unit_named(rows[0].unit)
        listed = " + ".join(str(value) for value in values) if len(values) <= LISTED else None
        decimals: int | None = None
        match step:
            case ValueStep():
                exact = Fraction(values[0])
                value = values[0]
                text = f"{step.name} = {value}"
            case SumStep():
                exact = arithmetic.total(values)
                value = cast("Decimal", arithmetic.exact_decimal(exact))
                text = f"{step.name} = {listed or f'the sum of {len(values)} cells'} = {value}"
            case MeanStep():
                exact = arithmetic.mean(values)
                value = arithmetic.round_away(exact, step.decimals)
                decimals = step.decimals
                summed = f"({listed})" if listed else f"the sum of {len(values)} cells"
                text = (
                    f"{step.name} = {summed} / {len(values)} = {arithmetic.fraction_text(exact)}"
                    f", rounded to {value}"
                )
        return StepResult(
            name=step.name,
            operation=step.operation,
            inputs=(),
            snapshot=step.snapshot,
            sql=(sql,),
            parameters=(parameters,),
            arithmetic=text,
            exact=arithmetic.fraction_text(exact),
            value=value,
            decimals=decimals,
            unit=unit,
        )

    def rank(self, step: RankStep) -> StepResult:
        sql, parameters, rows = self.select(step)
        snapshot = self.snapshots[step.snapshot]
        codes = [dimension.code for dimension in snapshot.manifest.dimensions]
        direction = "DESC" if step.order == "highest" else "ASC"
        where = sql[sql.index(" WHERE ") : sql.index(" ORDER BY ")]
        columns = ", ".join(quoted(code) for code in codes)
        ranking = (
            f"SELECT {columns}, value, rank() OVER (ORDER BY value {direction}) AS position "  # noqa: S608 - quoted names, bound codes
            f"FROM {self.tables[step.snapshot]}{where} QUALIFY position <= ? "
            f"ORDER BY position, {quoted(step.by)}"
        )
        cursor = self.connection.execute(ranking, [*parameters, step.count])
        check_types(cursor, step.name)
        labels = {
            dimension.code: {value.code: value.label for value in dimension.values}
            for dimension in snapshot.manifest.dimensions
        }
        ranked = tuple(
            RankedCell(
                position=cast("int", row[len(codes) + 1]),
                codes=tuple(cast("Sequence[str]", row[: len(codes)])),
                labels=tuple(
                    labels[code][cast("str", row[index])] for index, code in enumerate(codes)
                ),
                value=cast("Decimal", row[len(codes)]),
            )
            for row in cursor.fetchall()
        )
        text = (
            f"{step.name} = the {step.count} {step.order} of {len(rows)} cells by {step.by}, "
            f"with ties of the last one: {len(ranked)} cells"
        )
        return StepResult(
            name=step.name,
            operation=step.operation,
            inputs=(),
            snapshot=step.snapshot,
            sql=(sql, ranking),
            parameters=(parameters, (*parameters, str(step.count))),
            arithmetic=text,
            exact=None,
            value=None,
            decimals=None,
            unit=unit_named(rows[0].unit),
            ranked=ranked,
        )

    def combine(
        self, step: BinaryStep | DivideStep | ChangePercentStep | SharePercentStep
    ) -> StepResult:
        names = inputs(step)
        first, second = (self.outcomes[name] for name in names)
        a, b = Fraction(first.value), Fraction(second.value)
        decimals: int | None = None
        try:
            match step:
                case BinaryStep(operation="add" | "subtract"):
                    if not same_unit(first.unit, second.unit):
                        raise CalculationError(
                            f"mixes units: {first.unit.text()} and {second.unit.text()}", step.name
                        )
                    adding = step.operation == "add"
                    exact = a + b if adding else a - b
                    sign = "+" if adding else "-"
                    unit = first.unit
                    if not adding and first.unit.kind == "per cent":
                        unit = ResultUnit(kind="percentage points")
                    text = f"{step.name} = {first.value} {sign} {second.value}"
                case BinaryStep():
                    exact = a * b
                    unit = product(first.unit, second.unit, TIMES)
                    text = f"{step.name} = {first.value} {TIMES} {second.value}"
                case DivideStep():
                    exact = arithmetic.quotient(a, b)
                    decimals = step.decimals
                    unit = (
                        ResultUnit(kind="ratio")
                        if same_unit(first.unit, second.unit)
                        else product(first.unit, second.unit, "/")
                    )
                    text = f"{step.name} = {first.value} / {second.value}"
                case ChangePercentStep():
                    exact = arithmetic.change_percent(a, b)
                    decimals = step.decimals
                    unit = ResultUnit(kind="per cent")
                    text = f"{step.name} = ({second.value} - {first.value}) / {first.value} {TIMES} 100"
                case SharePercentStep():
                    exact = arithmetic.share_percent(a, b)
                    decimals = step.decimals
                    unit = ResultUnit(kind="per cent")
                    text = f"{step.name} = {first.value} / {second.value} {TIMES} 100"
        except ZeroDivisionError as error:
            raise CalculationError("divides by zero", step.name) from error
        if decimals is None:
            value = cast("Decimal", arithmetic.exact_decimal(exact))
            text = f"{text} = {value}"
        else:
            value = arithmetic.round_away(exact, decimals)
            text = f"{text} = {arithmetic.fraction_text(exact)}, rounded to {value}"
        return StepResult(
            name=step.name,
            operation=step.operation,
            inputs=names,
            snapshot=None,
            sql=(),
            parameters=(),
            arithmetic=text,
            exact=arithmetic.fraction_text(exact),
            value=value,
            decimals=decimals,
            unit=unit,
        )


def product(left: ResultUnit, right: ResultUnit, operator: str) -> ResultUnit:
    """Return the unit of a product or a quotient of two results."""
    return ResultUnit(kind="amount", name=f"{left.text()} {operator} {right.text()}")


def check_types(cursor: duckdb.DuckDBPyConnection, step: str) -> None:
    """Refuse a result column of a floating-point type."""
    for column in cursor.description or []:
        kind = str(column[1])
        if kind in FLOATING:
            raise CalculationError(f"got the column {column[0]} as {kind}, a binary number", step)

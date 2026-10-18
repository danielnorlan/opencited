"""The calculation engine: checks a plan, selects cells with DuckDB and computes exactly.

The engine checks the whole plan before it runs any step. It then loads the snapshots
that the plan names into an in-memory DuckDB database, from private copies of their
hashed Parquet bytes, turns off external access and locks the configuration, and only
then runs the SQL it generates, with every code bound as a parameter. DuckDB selects
and ranks cells; all arithmetic runs on exact rational numbers in
``opencited.calc.arithmetic`` (ADR 0011).
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, localcontext
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
from opencited.snapshots import (
    MAX_DIGITS,
    Snapshot,
    SnapshotError,
    SnapshotStore,
    digits,
    private_copy,
    quoted,
    scale_of,
)
from opencited.values import exact_text

# A step selects at most this many cells, as a query does (spec 0002).
MAX_CELLS = 5000
# A cell's unit counts as percentage points, or as per cent, when its name holds one of
# the words that spec 0003 lists for them, or the per cent sign, in any case, so that
# a name such as "Prosent av befolkningen" counts too.
PER_CENT = ("prosent", "per cent", "percent", "%")
PERCENTAGE_POINTS = ("prosentpoeng", "percentage point")
# A unit's own multiplier: a power of ten of a thousand or more that stands as a token
# of its own, not the base of an index such as 1998=1000, or a scale word of spec 0003.
UNIT_NUMBER = re.compile(r"(?<![\w.,=])([0-9]{1,3}(?: [0-9]{3})+|[0-9]+)(?![\w.,])")
POWER_OF_TEN = re.compile(r"10{3,}")
SCALE_WORDS = (
    (9, ("mrd.", "milliard", "milliarder", "billion", "billions")),
    (6, ("mill.", "million", "millioner", "millions")),
    (3, ("tusen", "thousand", "thousands")),
)
# What follows "per" in a unit names a rate, such as "Fødte per tusen innbyggere".
PER = re.compile(r"(?<!\w)per(?!\w)")
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
    """A selected cell, with its value as SSB wrote it."""

    codes: tuple[str, ...]
    value: Decimal | None
    symbol: str | None
    unit: str | None


def unit_scale(name: str | None) -> int:
    """Return the power of ten that a unit's name multiplies a value by.

    A name names a multiplier when it holds a power of ten of a thousand or more, as
    "1000 personer", "1 000 kr" and "NOK 1000" do, or a scale word, as "mill. kr" and
    "NOK million" do. What follows "per" names a rate, so "per 1000" and "Fødte per
    tusen innbyggere" name none, and nor do other names, such as "kg" (spec 0003,
    Matching values, item 6). A per cent unit has none either; ``unit_named`` sees to
    that.
    """
    if name is None:
        return 0
    word = " ".join(name.replace("\u00a0", " ").casefold().split())
    word = PER.split(word, maxsplit=1)[0]
    for match in UNIT_NUMBER.finditer(word):
        digits = match[1].replace(" ", "")
        if POWER_OF_TEN.fullmatch(digits):
            return len(digits) - 1
    for scale, words in SCALE_WORDS:
        for item in words:
            # A word with a point may run on into the unit, as in "mill.kr".
            end = "" if item.endswith(".") else r"(?!\w)"
            if re.search(rf"(?<![\w.]){re.escape(item)}{end}", word):
                return scale
    return 0


def unit_named(name: str | None) -> ResultUnit:
    """Return the unit of a cell, which is per cent or percentage points when it says so."""
    word = name.lower() if name is not None else ""
    if any(points in word for points in PERCENTAGE_POINTS):
        return ResultUnit(kind="percentage points", name=name)
    if any(percent in word for percent in PER_CENT):
        return ResultUnit(kind="per cent", name=name)
    return ResultUnit(kind="amount", name=name, scale=unit_scale(name))


def same_unit(left: ResultUnit, right: ResultUnit) -> bool:
    if left.kind != right.kind:
        return False
    return left.kind != "amount" or left.name == right.name


def base_text(value: Decimal, scale: int) -> str:
    """Return a value in its unit's base, as a calculation line writes it."""
    with localcontext() as context:
        context.prec = 100
        return text(value.scaleb(scale))


def text(value: Decimal) -> str:
    """Return a value in fixed point, as the evidence gate reads numbers."""
    return exact_text(value)


def checked(value: Decimal, step: str) -> Decimal:
    """Refuse a result that needs more digits than DuckDB's widest decimal type.

    A value takes its integer digits and its decimals, so this refuses more than 38
    significant digits, and also a value so small that its decimals pass 38.
    """
    needed = digits(value, scale_of(value))
    if needed > MAX_DIGITS:
        raise CalculationError(
            f"gives a result that needs {needed} digits, more than {MAX_DIGITS}", step
        )
    return value


class Engine:
    """Runs calculation plans over the snapshots in one store."""

    def __init__(self, store: SnapshotStore) -> None:
        self.store = store

    def run(self, plan: Plan) -> CalculationResult:
        """Check a plan, run every step and return the steps with their results."""
        snapshots = self._check(plan)
        connection, tables = locked(snapshots)
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
    snapshots: Mapping[str, Snapshot],
) -> tuple[duckdb.DuckDBPyConnection, dict[str, str]]:
    """Return an in-memory database with the snapshots loaded, and the table of each.

    Each snapshot is loaded from a private copy of the Parquet bytes that reading it
    hashed. Afterwards the database can read no file and change none of its settings,
    so the SQL that the engine generates can only read the loaded tables.
    """
    tables = {
        snapshot_id: f"snapshot_{index}" for index, snapshot_id in enumerate(snapshots, start=1)
    }
    connection = duckdb.connect(":memory:")
    try:
        for snapshot_id, table in tables.items():
            with private_copy(snapshots[snapshot_id].parquet) as path:
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


def selection_of(step: Step, snapshot: Snapshot | None = None) -> dict[str, tuple[str, ...]]:
    """Return the codes a step selects; a ranking leaving out ``by`` takes all its codes."""
    match step:
        case ValueStep():
            return {code: (value,) for code, value in step.codes.items()}
        case SumStep() | MeanStep():
            return dict(step.selection)
        case RankStep():
            selection = dict(step.selection)
            if snapshot is not None and step.by not in selection:
                for dimension in snapshot.manifest.dimensions:
                    if dimension.code == step.by:
                        selection[step.by] = tuple(value.code for value in dimension.values)
            return selection
        case _:
            return {}


def check_selection(step: Step, snapshot: Snapshot) -> None:
    """Refuse a selection that misses a dimension, names an unknown code or is too large."""
    dimensions = {dimension.code: dimension for dimension in snapshot.manifest.dimensions}
    if isinstance(step, RankStep) and step.by not in dimensions:
        raise CalculationError(f"ranks by {step.by!r}, which the snapshot lacks", step.name)
    selection = selection_of(step, snapshot)
    unknown = sorted(set(selection) - set(dimensions))
    if unknown:
        raise CalculationError(f"names {', '.join(unknown)}, which the snapshot lacks", step.name)
    if isinstance(step, RankStep):
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
            self.outcomes[step.name] = Outcome(result.value, result.unit)
        return result

    def where(
        self, step: ValueStep | SumStep | MeanStep | RankStep
    ) -> tuple[list[str], str, list[str]]:
        """Return the dimension codes, the WHERE clause and the codes it binds."""
        snapshot = self.snapshots[step.snapshot]
        selection = selection_of(step, snapshot)
        codes = [dimension.code for dimension in snapshot.manifest.dimensions]
        clauses: list[str] = []
        parameters: list[str] = []
        for code in codes:
            chosen = selection[code]
            clauses.append(f"{quoted(code)} IN ({', '.join('?' * len(chosen))})")
            parameters.extend(chosen)
        return codes, " AND ".join(clauses), parameters

    def select(
        self, step: ValueStep | SumStep | MeanStep | RankStep
    ) -> tuple[str, tuple[str, ...], list[Row]]:
        """Return the SQL, its parameters and the cells that a step selects."""
        codes, where, parameters = self.where(step)
        columns = ", ".join(quoted(code) for code in codes)
        sql = (
            f"SELECT {columns}, value_text, symbol, unit FROM {self.tables[step.snapshot]} "  # noqa: S608 - quoted names, bound codes
            f"WHERE {where} ORDER BY {columns}"
        )
        cursor = self.connection.execute(sql, parameters)
        check_types(cursor, step.name)
        count = len(codes)
        rows = [
            Row(
                codes=tuple(cast("Sequence[str]", row[:count])),
                value=Decimal(cast("str", row[count])) if row[count] is not None else None,
                symbol=cast("str | None", row[count + 1]),
                unit=cast("str | None", row[count + 2]),
            )
            for row in cursor.fetchall()
        ]
        expected = math.prod(
            len(chosen) for chosen in selection_of(step, self.snapshots[step.snapshot]).values()
        )
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
        listed = " + ".join(text(value) for value in values) if len(values) <= LISTED else None
        decimals: int | None = None
        match step:
            case ValueStep():
                exact = Fraction(values[0])
                value = checked(values[0], step.name)
                line = f"{step.name} = {text(value)}"
            case SumStep():
                exact = arithmetic.total(values)
                value = checked(cast("Decimal", arithmetic.exact_decimal(exact)), step.name)
                summed = listed or f"the sum of {len(values)} cells"
                line = f"{step.name} = {summed} = {text(value)}"
            case MeanStep():
                exact = arithmetic.mean(values)
                value = checked(arithmetic.round_away(exact, step.decimals), step.name)
                decimals = step.decimals
                summed = f"({listed})" if listed else f"the sum of {len(values)} cells"
                line = (
                    f"{step.name} = {summed} / {len(values)} = {arithmetic.fraction_text(exact)}"
                    f", rounded to {text(value)}"
                )
        return StepResult(
            name=step.name,
            operation=step.operation,
            inputs=(),
            snapshot=step.snapshot,
            sql=(sql,),
            parameters=(parameters,),
            arithmetic=line,
            exact=arithmetic.fraction_text(exact),
            value=value,
            decimals=decimals,
            unit=unit,
        )

    def rank(self, step: RankStep) -> StepResult:
        sql, parameters, rows = self.select(step)
        snapshot = self.snapshots[step.snapshot]
        codes, where, _ = self.where(step)
        direction = "DESC" if step.order == "highest" else "ASC"
        columns = ", ".join(quoted(code) for code in codes)
        # The place is the last column, sorted by its number, so no alias can collide
        # with a dimension code.
        window = f"rank() OVER (ORDER BY value {direction})"
        place = len(codes) + 2
        ranking = (
            f"SELECT {columns}, value_text, {window} FROM {self.tables[step.snapshot]} "  # noqa: S608 - quoted names, bound codes
            f"WHERE {where} QUALIFY {window} <= ? ORDER BY {place}, {quoted(step.by)}"
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
                value=Decimal(cast("str", row[len(codes)])),
            )
            for row in cursor.fetchall()
        )
        line = (
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
            arithmetic=line,
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
        one, two = text(first.value), text(second.value)
        if isinstance(step, ChangePercentStep | SharePercentStep) and (
            first.unit.scale != second.unit.scale
        ):
            # A per cent of two amounts in units of different powers of ten, such as
            # 1000 personer and personer, is taken of both in the base unit.
            a, b = a * 10**first.unit.scale, b * 10**second.unit.scale
            one, two = (
                base_text(first.value, first.unit.scale),
                base_text(second.value, second.unit.scale),
            )
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
                    unit = first.unit
                    if not adding and first.unit.kind == "per cent":
                        unit = ResultUnit(kind="percentage points")
                    line = f"{step.name} = {one} {'+' if adding else '-'} {two}"
                case BinaryStep():
                    exact = a * b
                    unit = product(first.unit, second.unit, TIMES)
                    line = f"{step.name} = {one} {TIMES} {two}"
                case DivideStep():
                    exact = arithmetic.quotient(a, b)
                    decimals = step.decimals
                    unit = (
                        ResultUnit(kind="ratio")
                        if same_unit(first.unit, second.unit)
                        else product(first.unit, second.unit, "/")
                    )
                    line = f"{step.name} = {one} / {two}"
                case ChangePercentStep():
                    exact = arithmetic.change_percent(a, b)
                    decimals = step.decimals
                    unit = ResultUnit(kind="per cent")
                    line = f"{step.name} = ({two} - {one}) / {one} {TIMES} 100"
                case SharePercentStep():
                    exact = arithmetic.share_percent(a, b)
                    decimals = step.decimals
                    unit = ResultUnit(kind="per cent")
                    line = f"{step.name} = {one} / {two} {TIMES} 100"
        except ZeroDivisionError as error:
            raise CalculationError("divides by zero", step.name) from error
        if decimals is None:
            value = checked(cast("Decimal", arithmetic.exact_decimal(exact)), step.name)
            line = f"{line} = {text(value)}"
        else:
            value = checked(arithmetic.round_away(exact, decimals), step.name)
            line = f"{line} = {arithmetic.fraction_text(exact)}, rounded to {text(value)}"
        return StepResult(
            name=step.name,
            operation=step.operation,
            inputs=names,
            snapshot=None,
            sql=(),
            parameters=(),
            arithmetic=line,
            exact=arithmetic.fraction_text(exact),
            value=value,
            decimals=decimals,
            unit=unit,
        )


def product(left: ResultUnit, right: ResultUnit, operator: str) -> ResultUnit:
    """Return the unit of a product or a quotient of two results, with its scale."""
    scale = left.scale + right.scale if operator == TIMES else left.scale - right.scale
    return ResultUnit(kind="amount", name=f"{left.text()} {operator} {right.text()}", scale=scale)


def check_types(cursor: duckdb.DuckDBPyConnection, step: str) -> None:
    """Refuse a result column of a floating-point type."""
    for column in cursor.description or []:
        kind = str(column[1])
        if kind in FLOATING:
            raise CalculationError(f"got the column {column[0]} as {kind}, a binary number", step)

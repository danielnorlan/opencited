"""Calculation results: every step with its inputs, SQL, exact and rounded result and unit.

Values are decimal strings in JSON and exact fractions as ``numerator/denominator``, so
no value in a result is a ``float``. ``readable`` writes each step as the SQL that
selected its cells or as arithmetic with the numbers filled in.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from opencited.model import Frozen, Sha256Hex
from opencited.provenance import ProvenanceRecord
from opencited.values import ExactDecimal

FRACTION_PATTERN = r"^-?[0-9]+(/[0-9]+)?$"


class ResultUnit(Frozen):
    """The unit of a result.

    ``kind`` says how a number with this unit may be written: as an amount, as per
    cent, as percentage points or as a ratio. ``name`` is the source's unit for an
    amount, or the product or quotient of units, and is empty for the other kinds.
    """

    kind: Literal["amount", "per cent", "percentage points", "ratio"]
    name: str | None = None

    def text(self) -> str:
        if self.kind == "amount":
            return self.name or "no unit"
        return self.kind


class RankedCell(Frozen):
    """A cell of a ranking, with its codes and their labels."""

    position: int = Field(ge=1)
    codes: tuple[str, ...]
    labels: tuple[str, ...]
    value: ExactDecimal


class StepResult(Frozen):
    name: str
    operation: str
    inputs: tuple[str, ...] = Field(description="The earlier steps that the step read.")
    snapshot: Sha256Hex | None = Field(description="The snapshot that the step read.")
    sql: tuple[str, ...] = Field(description="The SQL that selected the step's cells.")
    parameters: tuple[tuple[str, ...], ...] = Field(
        description="The codes bound to each SQL statement, in order."
    )
    arithmetic: str = Field(description="The calculation with the numbers filled in.")
    exact: str | None = Field(pattern=FRACTION_PATTERN, description="The exact result.")
    value: ExactDecimal | None = Field(description="The result that later steps use.")
    decimals: int | None = Field(description="The decimals the result was rounded to.")
    unit: ResultUnit
    ranked: tuple[RankedCell, ...] = ()


class SnapshotUse(Frozen):
    """A snapshot that a calculation read, with the provenance of its data."""

    snapshot_id: Sha256Hex
    provenance: ProvenanceRecord


class CalculationResult(Frozen):
    steps: tuple[StepResult, ...]
    snapshots: tuple[SnapshotUse, ...]

    def step(self, name: str) -> StepResult:
        return next(step for step in self.steps if step.name == name)

    def readable(self) -> str:
        """Return each step as its SQL or its arithmetic, and the result with its unit."""
        lines: list[str] = []
        for step in self.steps:
            lines.append(f"{step.name} ({step.operation}):")
            for sql, parameters in zip(step.sql, step.parameters, strict=True):
                lines.append(f"  {sql}")
                if parameters:
                    lines.append(f"    with {', '.join(parameters)}")
            lines.append(f"  {step.arithmetic}")
            for cell in step.ranked:
                lines.append(
                    f"  {cell.position}. {', '.join(cell.labels)}: {cell.value} {step.unit.text()}"
                )
        return "\n".join(lines)

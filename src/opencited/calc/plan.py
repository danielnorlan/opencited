"""Calculation plans: typed steps over snapshots and earlier results (spec 0002).

A plan is a list of steps. A step reads cells from a snapshot, named by its ID, or
combines the results of earlier steps, named by their step names. The models are
strict, so a plan with an unknown operation, a missing field or a field of the wrong
type is refused before anything runs.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints

from opencited.model import Frozen, Sha256Hex

MAX_STEPS = 20
MAX_DECIMALS = 20
MAX_RANK = 10

StepName = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,39}$"),
    Field(description="A name of letters, digits and underscores, starting with a letter."),
]
Snapshot = Annotated[Sha256Hex, Field(description="The ID of a snapshot, its response_sha256.")]
Decimals = Annotated[
    int,
    Field(ge=0, le=MAX_DECIMALS, description="The decimals to round the result to, once."),
]
Codes = Annotated[
    dict[str, str],
    Field(description="One code for every dimension of the snapshot."),
]
Selection = Annotated[
    dict[str, tuple[str, ...]],
    Field(description="One or more codes for every dimension of the snapshot."),
]


class ValueStep(Frozen):
    """The value of one cell."""

    name: StepName
    operation: Literal["value"]
    snapshot: Snapshot
    codes: Codes


class SumStep(Frozen):
    """The sum of the selected cells."""

    name: StepName
    operation: Literal["sum"]
    snapshot: Snapshot
    selection: Selection


class MeanStep(Frozen):
    """The mean of the selected cells, rounded."""

    name: StepName
    operation: Literal["mean"]
    snapshot: Snapshot
    selection: Selection
    decimals: Decimals


class BinaryStep(Frozen):
    """The exact sum, difference or product of two earlier results."""

    name: StepName
    operation: Literal["add", "subtract", "multiply"]
    left: StepName
    right: StepName


class DivideStep(Frozen):
    """The quotient of two earlier results, rounded."""

    name: StepName
    operation: Literal["divide"]
    left: StepName
    right: StepName
    decimals: Decimals


class ChangePercentStep(Frozen):
    """(new - old) / old x 100, rounded."""

    name: StepName
    operation: Literal["change_percent"]
    old: StepName
    new: StepName
    decimals: Decimals


class SharePercentStep(Frozen):
    """part / total x 100, rounded."""

    name: StepName
    operation: Literal["share_percent"]
    part: StepName
    total: StepName
    decimals: Decimals


class RankStep(Frozen):
    """The cells with the highest or lowest values, with ties of the last one included.

    The selection fixes every dimension apart from ``by``, with one code each. It may
    list codes of ``by``, or leave the dimension out to rank all its codes.
    """

    name: StepName
    operation: Literal["rank"]
    snapshot: Snapshot
    selection: Selection
    by: str = Field(description="The dimension whose codes are ranked.")
    order: Literal["highest", "lowest"]
    count: int = Field(ge=1, le=MAX_RANK, description="How many cells to return, 1 to 10.")


type Step = (
    ValueStep
    | SumStep
    | MeanStep
    | BinaryStep
    | DivideStep
    | ChangePercentStep
    | SharePercentStep
    | RankStep
)


class Plan(Frozen):
    """A calculation plan: at most 20 steps, run in order."""

    steps: tuple[
        Annotated[
            ValueStep
            | SumStep
            | MeanStep
            | BinaryStep
            | DivideStep
            | ChangePercentStep
            | SharePercentStep
            | RankStep,
            Field(discriminator="operation"),
        ],
        ...,
    ] = Field(min_length=1, max_length=MAX_STEPS)


def inputs(step: Step) -> tuple[str, ...]:
    """Return the names of the earlier steps that a step reads."""
    match step:
        case BinaryStep() | DivideStep():
            return (step.left, step.right)
        case ChangePercentStep():
            return (step.old, step.new)
        case SharePercentStep():
            return (step.part, step.total)
        case _:
            return ()


def plan_schema() -> dict[str, Any]:
    """Return the JSON schema of a plan, which the calculate tool publishes."""
    return Plan.model_json_schema()

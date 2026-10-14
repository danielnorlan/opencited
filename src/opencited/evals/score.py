"""The scorer: whether an answer or a refusal is correct for a question (spec 0005).

The scorer is deterministic and asks no model. It reads an answer's numbers with the
rules of the evidence gate. An expected number is in an answer when a number there
may be that value under the gate's rules for unit and rounding, and shows at least the
required decimals in the value's own unit. A scale word lowers what a number shows:
"5,6 millioner" shows a count to one decimal of a million, which is five places short
of a whole number.
"""

from __future__ import annotations

from collections.abc import Sequence
from fractions import Fraction
from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from opencited.agent.answer import Answer, Refusal, RunUsage
from opencited.agent.run import Attempt
from opencited.calc.result import FRACTION_PATTERN
from opencited.gate import Evidence, Number, check, normalise, read_numbers
from opencited.gate.check import UnitKind, decimals, fits_value
from opencited.model import Frozen
from opencited.sources import Language

type Category = Literal[
    "lookup",
    "change",
    "share",
    "sum",
    "ranking",
    "false premise",
    "unanswerable",
    "out of scope",
]
CATEGORIES: tuple[Category, ...] = (
    "lookup",
    "change",
    "share",
    "sum",
    "ranking",
    "false premise",
    "unanswerable",
    "out of scope",
)

QuestionId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")]


class ExpectedNumber(Frozen):
    """A number the answer must hold: a step of the reference plan, run on the day's data."""

    step: str
    exact: str = Field(pattern=FRACTION_PATTERN, description="The step's exact result.")
    kind: UnitKind
    decimals: int = Field(ge=0, le=20, description="The decimals the answer shows at least.")


class Expectation(Frozen):
    """What a question expects, with the numbers its reference plan gave in this run."""

    question_id: QuestionId
    language: Language
    category: Category
    outcome: Literal["answer", "refusal"]
    numbers: tuple[ExpectedNumber, ...] = ()
    label: str | None = Field(default=None, description="The label a ranking answer holds.")

    @model_validator(mode="after")
    def check_outcome(self) -> Self:
        if self.outcome == "answer" and not self.numbers:
            raise ValueError("a question with an answer expects at least one number")
        if self.outcome == "refusal" and (self.numbers or self.label is not None):
            raise ValueError("a question that should be refused expects no number or label")
        return self


class Failure(Frozen):
    """A run that stopped with an error, with what it used when known."""

    message: str
    usage: RunUsage | None


class NumberFound(Frozen):
    """An expected number, and the text of the answer's number that holds it, if any."""

    step: str
    text: str | None


class Verdict(Frozen):
    question_id: QuestionId
    language: Language
    category: Category
    expected: Literal["answer", "refusal"]
    outcome: Literal["answer", "refusal", "failure"]
    correct: bool
    reason: str | None = Field(description="The refusal's reason or the run's error.")
    passes_gate: bool | None = Field(description="Whether the answer passed the gate again.")
    numbers: tuple[NumberFound, ...]
    label_found: bool | None
    attempts: tuple[Attempt, ...]
    usage: RunUsage | None


def shown_decimals(number: Number) -> int:
    """Return the decimals a number shows in its value's unit, after its scale word."""
    if number.value is None:
        raise ValueError(f"{number.text!r} has no value")
    return decimals(number.value) - number.scale


def holder(expected: ExpectedNumber, numbers: Sequence[Number]) -> str | None:
    """Return the text of the first number that holds an expected number, or None."""
    exact = Fraction(expected.exact)
    for number in numbers:
        if (
            number.kind == "number"
            and shown_decimals(number) >= expected.decimals
            and fits_value(number, exact, expected.kind)
        ):
            return number.text
    return None


def verdict(
    expectation: Expectation,
    *,
    outcome: Literal["answer", "refusal", "failure"],
    correct: bool,
    reason: str | None = None,
    passes_gate: bool | None = None,
    numbers: tuple[NumberFound, ...] = (),
    label_found: bool | None = None,
    attempts: tuple[Attempt, ...] = (),
    usage: RunUsage | None = None,
) -> Verdict:
    return Verdict(
        question_id=expectation.question_id,
        language=expectation.language,
        category=expectation.category,
        expected=expectation.outcome,
        outcome=outcome,
        correct=correct,
        reason=reason,
        passes_gate=passes_gate,
        numbers=numbers,
        label_found=label_found,
        attempts=attempts,
        usage=usage,
    )


def score(
    expectation: Expectation, result: Answer | Refusal | Failure, evidence: Sequence[Evidence]
) -> Verdict:
    """Return the verdict on one question's result, with the evidence of its run."""
    if isinstance(result, Failure):
        return verdict(
            expectation,
            outcome="failure",
            correct=False,
            reason=result.message,
            usage=result.usage,
        )
    if isinstance(result, Refusal):
        return verdict(
            expectation,
            outcome="refusal",
            correct=expectation.outcome == "refusal",
            reason=result.reason,
            attempts=result.attempts,
            usage=result.usage,
        )
    language = expectation.language
    text = normalise(result.text)
    numbers = read_numbers(text, language)
    found = tuple(
        NumberFound(step=expected.step, text=holder(expected, numbers))
        for expected in expectation.numbers
    )
    label = expectation.label
    label_found = None if label is None else label.casefold() in text.casefold()
    passes = check(result.text, language, evidence).passed
    correct = (
        expectation.outcome == "answer"
        and passes
        and all(item.text is not None for item in found)
        and label_found is not False
    )
    return verdict(
        expectation,
        outcome="answer",
        correct=correct,
        passes_gate=passes,
        numbers=found,
        label_found=label_found,
        attempts=result.attempts,
        usage=result.usage,
    )


__all__ = [
    "CATEGORIES",
    "Category",
    "Expectation",
    "ExpectedNumber",
    "Failure",
    "NumberFound",
    "Verdict",
    "score",
]

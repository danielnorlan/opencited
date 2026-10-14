"""The scorer: whether an answer or a refusal is correct for a question (spec 0005).

The scorer is deterministic and asks no model. It reads an answer with the evidence
gate. An expected number is in an answer when a shown number that the gate matched as
a value may be that value under the gate's rules for unit and rounding, and shows at
least the required decimals in the value's own unit. Periods, dates, table IDs, label
numbers and numbers in HTML comments do not count. A scale word lowers what a number
shows: "5,6 millioner" shows a count to one decimal of a million, which is five places
short of a whole number.

A label counts when it stands in the shown text as a whole: the comparison ignores
case and the kind of dash and space, and the label may not run on into a letter, a
digit or a hyphen. A label that begins another one, such as "Troms" in "Troms og
Finnmark", still counts; the scorer cannot tell them apart.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from fractions import Fraction
from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from opencited.agent.answer import Answer, Refusal, RunUsage
from opencited.agent.run import Attempt
from opencited.calc.result import FRACTION_PATTERN
from opencited.gate import Evidence, Number, Report, check, normalise
from opencited.gate.check import COMMENT, UnitKind, decimals, fits_value, hidden_spans
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

REFUSED: frozenset[Category] = frozenset({"unanswerable", "out of scope"})

QuestionId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")]
SPACES = str.maketrans(dict.fromkeys("\u00a0\u202f\u2009", " "))
DASHES = str.maketrans(dict.fromkeys("\u2010\u2011\u2012\u2013\u2014\u2212", "-"))


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
    label: str | None = Field(
        default=None, min_length=1, description="The label a ranking answer holds."
    )

    @model_validator(mode="after")
    def check_outcome(self) -> Self:
        refused = self.category in REFUSED
        if refused != (self.outcome == "refusal"):
            raise ValueError(
                f"a question of the category {self.category} expects the other outcome"
            )
        if self.outcome == "answer" and not self.numbers:
            raise ValueError("a question with an answer expects at least one number")
        if self.outcome == "refusal" and (self.numbers or self.label is not None):
            raise ValueError("a question that should be refused expects no number or label")
        if (self.category == "ranking") != (self.label is not None):
            raise ValueError("a ranking question expects a label, and no other question does")
        return self


class Failure(Frozen):
    """A run that stopped with an error, with what it used and what the gate checked."""

    message: str
    usage: RunUsage | None
    attempts: tuple[Attempt, ...] = ()
    retries: int = 0


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
    retries: int
    usage: RunUsage | None


def shown_decimals(number: Number) -> int:
    """Return the decimals a number shows in its value's unit, after its scale word."""
    if number.value is None:
        raise ValueError(f"{number.text!r} has no value")
    return decimals(number.value) - number.scale


def shown_values(text: str, report: Report) -> list[Number]:
    """Return the numbers that the gate matched as values, outside HTML comments."""
    hidden = hidden_spans(normalise(text))
    return [
        Number(
            position=finding.position,
            end=finding.end,
            text=finding.text,
            kind="number",
            value=finding.value,
            scale=finding.scale,
            unit=finding.unit,
        )
        for finding in report.findings
        if finding.kind == "value"
        and finding.outcome == "matched"
        and not any(start <= finding.position < end for start, end in hidden)
    ]


def holder(expected: ExpectedNumber, numbers: Sequence[Number]) -> str | None:
    """Return the text of the first number that holds an expected number, or None."""
    exact = Fraction(expected.exact)
    for number in numbers:
        if shown_decimals(number) >= expected.decimals and fits_value(number, exact, expected.kind):
            return number.text
    return None


def folded(text: str) -> str:
    """Return a text in NFC, in lower case, with one kind of dash and of space."""
    return " ".join(normalise(text).translate(SPACES).translate(DASHES).casefold().split())


def has_label(text: str, label: str) -> bool:
    shown = folded(COMMENT.sub(" ", normalise(text)))
    return re.search(rf"(?<![\w-]){re.escape(folded(label))}(?![\w-])", shown) is not None


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
    retries: int = 0,
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
        retries=retries,
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
            attempts=result.attempts,
            retries=result.retries,
            usage=result.usage,
        )
    if isinstance(result, Refusal):
        return verdict(
            expectation,
            outcome="refusal",
            correct=expectation.outcome == "refusal",
            reason=result.reason,
            attempts=result.attempts,
            retries=result.retries,
            usage=result.usage,
        )
    report = check(result.text, expectation.language, evidence)
    numbers = shown_values(result.text, report)
    found = tuple(
        NumberFound(step=expected.step, text=holder(expected, numbers))
        for expected in expectation.numbers
    )
    label = expectation.label
    label_found = None if label is None else has_label(result.text, label)
    passes = report.passed
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
        retries=result.retries,
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

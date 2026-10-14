"""The counts of a run: correct answers by category and language, refusals, blocks and use.

Rates are exact fractions rounded once to four decimals with ties away from zero, so
the same verdicts give the same report, and no rate is a binary number (spec 0005).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from fractions import Fraction

from pydantic import Field

from opencited.calc.arithmetic import round_away
from opencited.evals.score import CATEGORIES, Verdict
from opencited.model import Frozen
from opencited.values import ExactDecimal

RATE_DECIMALS = 4


class Rate(Frozen):
    """How many of how many, and the share rounded to four decimals; None of none."""

    count: int
    total: int
    rate: ExactDecimal | None


class Group(Frozen):
    name: str
    correct: Rate


class QuestionUse(Frozen):
    """What one question's run used; None where the run stopped before counting."""

    question_id: str
    tokens: int | None
    cost: ExactDecimal | None
    seconds: ExactDecimal | None


class ScoreReport(Frozen):
    questions: int
    correct: Rate
    by_category: tuple[Group, ...] = Field(description="Categories in the order of spec 0005.")
    by_language: tuple[Group, ...]
    refused_with_answer: Rate = Field(description="Refusals of questions that have an answer.")
    answered_should_refuse: Rate = Field(description="Answers to questions that should be refused.")
    failures: int = Field(description="Runs that stopped with an error.")
    first_attempt_blocked: Rate = Field(
        description="Runs with a checked reply whose first one the gate blocked."
    )
    first_attempt_uncited: Rate = Field(
        description="Runs with a checked reply whose first one the gate passed but cited nothing."
    )
    retries: int = Field(description="Replies sent back for a correction, in all runs.")
    usage: tuple[QuestionUse, ...]


def rate(count: int, total: int) -> Rate:
    value = round_away(Fraction(count, total), RATE_DECIMALS) if total else None
    return Rate(count=count, total=total, rate=value)


def share(verdicts: Sequence[Verdict], test: Callable[[Verdict], bool]) -> Rate:
    return rate(sum(1 for item in verdicts if test(item)), len(verdicts))


def groups(
    verdicts: Sequence[Verdict], names: Sequence[str], key: Callable[[Verdict], str]
) -> tuple[Group, ...]:
    found: list[Group] = []
    for name in names:
        members = [item for item in verdicts if key(item) == name]
        if members:
            found.append(Group(name=name, correct=share(members, lambda item: item.correct)))
    return tuple(found)


def use_of(item: Verdict) -> QuestionUse:
    usage = item.usage
    if usage is None:
        return QuestionUse(question_id=item.question_id, tokens=None, cost=None, seconds=None)
    return QuestionUse(
        question_id=item.question_id,
        tokens=usage.input_tokens + usage.output_tokens,
        cost=usage.cost,
        seconds=usage.seconds,
    )


def report_of(verdicts: Sequence[Verdict]) -> ScoreReport:
    """Return the counts of a run's verdicts."""
    with_answer = [item for item in verdicts if item.expected == "answer"]
    should_refuse = [item for item in verdicts if item.expected == "refusal"]
    checked = [item for item in verdicts if item.attempts]
    return ScoreReport(
        questions=len(verdicts),
        correct=share(verdicts, lambda item: item.correct),
        by_category=groups(verdicts, CATEGORIES, lambda item: item.category),
        by_language=groups(verdicts, ("no", "en"), lambda item: item.language),
        refused_with_answer=share(with_answer, lambda item: item.outcome == "refusal"),
        answered_should_refuse=share(should_refuse, lambda item: item.outcome == "answer"),
        failures=sum(1 for item in verdicts if item.outcome == "failure"),
        first_attempt_blocked=share(checked, lambda item: item.attempts[0] == "blocked"),
        first_attempt_uncited=share(checked, lambda item: item.attempts[0] == "uncited"),
        retries=sum(item.retries for item in verdicts),
        usage=tuple(use_of(item) for item in verdicts),
    )


__all__ = ["Group", "QuestionUse", "Rate", "ScoreReport", "report_of"]

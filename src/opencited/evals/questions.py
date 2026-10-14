"""Question sets: versioned questions with the outcome each expects (spec 0005).

A set is a JSONL file, one question a line, and its version is the file's name, such
as ``v1`` for ``evals/questions/v1.jsonl``. A question with an answer names the queries
its reference plan reads, as ``Q1``, ``Q2`` and so on, the plan, the steps whose
results the answer must hold with the decimals it must show, and for a ranking the
step whose first cell gives the label. Its numbers are never written down: a run
fetches the queries and runs the plan (``opencited.evals.reference``).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Annotated, Any, Literal, Self, cast

from pydantic import Field, StringConstraints, ValidationError, model_validator

from opencited.calc.plan import MeanStep, Plan, RankStep, Step, StepName, SumStep, ValueStep
from opencited.evals.score import REFUSED, Category, QuestionId
from opencited.model import Frozen, TableId
from opencited.sources import Language

QueryName = Annotated[str, StringConstraints(pattern=r"^Q[1-9][0-9]*$")]
# A stand-in snapshot ID for checking a plan before any query has run.
PLACEHOLDER = "0" * 64
VERSION = re.compile(r"v[1-9][0-9]*")


class QuestionError(ValueError):
    """A question set that cannot be read, with the line at fault."""


def plan_steps(steps: list[object]) -> list[dict[str, Any]]:
    if not all(isinstance(step, dict) for step in steps):
        raise ValueError("every step of the plan is an object")
    return cast("list[dict[str, Any]]", steps)


# Characters of SSB's selection expressions, such as * and top(3); the codes such an
# expression covers are known only when the query runs.
EXPRESSION = re.compile(r"[*?()\[\]]")


def read_codes(step: Step) -> dict[str, tuple[str, ...]]:
    """Return the codes that a step reads from its snapshot, by dimension."""
    if isinstance(step, ValueStep):
        return {dimension: (code,) for dimension, code in step.codes.items()}
    if isinstance(step, SumStep | MeanStep | RankStep):
        return dict(step.selection)
    return {}


def check_codes(name: str, codes: dict[str, tuple[str, ...]], query: Query) -> None:
    """Refuse a step that reads a code its query does not fetch, where that can be known."""
    for dimension, wanted in codes.items():
        fetched = query.selection.get(dimension)
        if fetched is None:
            raise ValueError(f"step {name!r} reads {dimension}, which {query.name} does not select")
        if any(EXPRESSION.search(code) for code in fetched):
            continue
        missing = [code for code in wanted if code not in fetched]
        if missing:
            raise ValueError(
                f"step {name!r} reads {dimension} {', '.join(missing)}, which {query.name} does not fetch"
            )


class Query(Frozen):
    """A query that a reference plan reads, named so that its steps can refer to it."""

    name: QueryName
    table_id: TableId
    selection: dict[str, tuple[str, ...]] = Field(min_length=1)
    language: Language


class NumberRef(Frozen):
    """A step whose result the answer must hold, and the decimals it must show at least.

    For a ranking step, ``position`` names the ranked cell whose value counts.
    """

    step: StepName
    decimals: int = Field(ge=0, le=20)
    position: int | None = Field(default=None, ge=1)


class AnswerExpected(Frozen):
    outcome: Literal["answer"]
    queries: tuple[Query, ...] = Field(min_length=1)
    plan: dict[str, Any]
    numbers: tuple[NumberRef, ...] = Field(min_length=1)
    label: StepName | None = Field(
        default=None, description="The ranking step whose first cell gives the label."
    )

    def checked_plan(self, snapshots: dict[str, str]) -> Plan:
        """Return the plan with each query name replaced by a snapshot ID."""
        steps = [
            {**step, "snapshot": snapshots[step["snapshot"]]} if "snapshot" in step else step
            for step in plan_steps(cast("list[object]", self.plan.get("steps", [])))
        ]
        return Plan.model_validate_json(json.dumps({**self.plan, "steps": steps}))

    @model_validator(mode="after")
    def check_references(self) -> Self:
        names = [query.name for query in self.queries]
        if len(set(names)) != len(names):
            raise ValueError("two queries have the same name")
        steps = self.plan.get("steps")
        if not isinstance(steps, list):
            raise ValueError("the plan has no list of steps")
        snapshots = [
            step["snapshot"]
            for step in plan_steps(cast("list[object]", steps))
            if "snapshot" in step
        ]
        if not all(isinstance(name, str) for name in snapshots):
            raise ValueError("a step names its query by a name such as Q1")
        if set(snapshots) != set(names):
            raise ValueError("the plan must read every query, and only the queries named")
        plan = self.checked_plan(dict.fromkeys(names, PLACEHOLDER))
        queries = {query.name: query for query in self.queries}
        for raw, step in zip(cast("list[dict[str, Any]]", steps), plan.steps, strict=True):
            if "snapshot" in raw:
                check_codes(step.name, read_codes(step), queries[raw["snapshot"]])
        by_name = {step.name: step for step in plan.steps}
        for number in self.numbers:
            step = by_name.get(number.step)
            if step is None:
                raise ValueError(f"the plan has no step {number.step!r}")
            if isinstance(step, RankStep) != (number.position is not None):
                raise ValueError(
                    f"a number of the ranking step {number.step!r} names a position, "
                    "and no other number does"
                )
            if (
                isinstance(step, RankStep)
                and number.position is not None
                and number.position > step.count
            ):
                raise ValueError(f"the ranking step {number.step!r} has no place {number.position}")
        if self.label is not None and not isinstance(by_name.get(self.label), RankStep):
            raise ValueError(f"the label step {self.label!r} is not a ranking step")
        return self


class RefusalExpected(Frozen):
    outcome: Literal["refusal"]
    reason: str | None = Field(
        default=None,
        min_length=1,
        description="Why the sources cannot answer the question, as checked when it was written.",
    )


class Question(Frozen):
    id: QuestionId
    language: Language
    text: str = Field(min_length=1)
    category: Category
    expected: Annotated[AnswerExpected | RefusalExpected, Field(discriminator="outcome")]

    @model_validator(mode="after")
    def check_category(self) -> Self:
        expected = self.expected
        if (self.category in REFUSED) != isinstance(expected, RefusalExpected):
            raise ValueError(
                f"a question of the category {self.category} expects the other outcome"
            )
        if isinstance(expected, AnswerExpected) and (
            (self.category == "ranking") != (expected.label is not None)
        ):
            raise ValueError("a ranking question expects a label, and no other question does")
        if isinstance(expected, AnswerExpected) and expected.label is not None:
            # The label is compared as the snapshot gives it, so the snapshot must be in
            # the language of the question.
            steps = plan_steps(cast("list[object]", expected.plan["steps"]))
            ranked = next(step for step in steps if step.get("name") == expected.label)
            query = next(item for item in expected.queries if item.name == ranked["snapshot"])
            if query.language != self.language:
                raise ValueError("the ranking's query must be in the language of the question")
        return self


class QuestionSet(Frozen):
    version: str
    questions: tuple[Question, ...]


def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [key for key, _ in pairs]
    repeated = sorted({key for key in keys if keys.count(key) > 1})
    if repeated:
        raise ValueError(f"the line names {', '.join(repeated)} more than once in one object")
    return dict(pairs)


def load_questions(path: Path) -> QuestionSet:
    """Read a question set, refusing a line that breaks the schema or repeats an ID."""
    if not VERSION.fullmatch(path.stem):
        raise QuestionError(f"{path.name}: a question set is named by its version, such as v1")
    questions: list[Question] = []
    seen: set[str] = set()
    # Lines end at a line feed only; splitlines would also break at U+2028 and U+0085,
    # which a JSON string may hold.
    for number, line in enumerate(path.read_text(encoding="utf-8").split("\n"), start=1):
        line = line.removesuffix("\r")
        if not line.strip():
            continue
        try:
            json.loads(line, object_pairs_hook=unique)
            question = Question.model_validate_json(line)
        except (ValueError, ValidationError) as error:
            raise QuestionError(f"{path.name}, line {number}: {error}") from error
        if question.id in seen:
            raise QuestionError(f"{path.name}, line {number}: the ID {question.id} is repeated")
        seen.add(question.id)
        questions.append(question)
    return QuestionSet(version=path.stem, questions=tuple(questions))


__all__ = [
    "AnswerExpected",
    "NumberRef",
    "Query",
    "Question",
    "QuestionError",
    "QuestionSet",
    "RefusalExpected",
    "load_questions",
]

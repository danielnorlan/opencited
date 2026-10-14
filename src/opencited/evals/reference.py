"""The reference run of a question: its queries fetched and its plan run (spec 0005).

A run fetches every query of a reference plan from the source when it runs, keeps
each as a snapshot and runs the plan in the calculation engine, so a revision at SSB
changes the expected answer and the agent's data together, and no expected number is
written by hand. A ranking's label is the label, in the query's language, of the
ranked dimension in the first cell; a tie for the first place leaves no single label.

A full run sends more requests than SSB's rate limit allows in a minute, so a query
that the limit holds back waits for as long as the limit says and is sent again, a
few times at most.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from fractions import Fraction

import anyio
import anyio.to_thread

from opencited.calc.engine import Engine
from opencited.calc.plan import Plan, RankStep
from opencited.calc.result import CalculationResult
from opencited.evals.questions import AnswerExpected, NumberRef, Question, RefusalExpected
from opencited.evals.score import Expectation, ExpectedNumber
from opencited.snapshots import SnapshotStore
from opencited.sources import QueryRetrieval, RateLimitedError
from opencited.sources.ssb import SsbClient

# Sends of one query after the first, when the rate limit holds it back.
PATIENCE = 3
# Seconds to wait when the limit names no time; SSB's limit counts a minute.
DEFAULT_WAIT = 60

type Sleep = Callable[[float], Awaitable[None]]


class ReferencePlanError(Exception):
    """A reference plan whose result cannot give what the question expects."""


@dataclass(frozen=True)
class Reference:
    """What a question expects in this run, the snapshots it read and the plan's result."""

    expectation: Expectation
    snapshots: tuple[tuple[str, str], ...]
    result: CalculationResult | None


def expected_number(result: CalculationResult, ref: NumberRef) -> ExpectedNumber:
    step = result.step(ref.step)
    if ref.position is None:
        exact = step.exact
        if exact is None:
            raise ReferencePlanError(f"step {ref.step!r} has no single result")
    else:
        cells = [cell for cell in step.ranked if cell.position == ref.position]
        if len(cells) != 1:
            raise ReferencePlanError(
                f"step {ref.step!r} has {len(cells)} cells at place {ref.position}, not one"
            )
        exact = str(Fraction(cells[0].value))
    return ExpectedNumber(step=ref.step, exact=exact, kind=step.unit.kind, decimals=ref.decimals)


def label_of(
    plan: Plan,
    result: CalculationResult,
    dimensions: Mapping[str, tuple[str, ...]],
    name: str,
) -> str:
    step = next(item for item in plan.steps if item.name == name)
    if not isinstance(step, RankStep):
        raise ReferencePlanError(f"step {name!r} is not a ranking")
    first = [cell for cell in result.step(name).ranked if cell.position == 1]
    if len(first) != 1:
        raise ReferencePlanError(f"step {name!r} has {len(first)} cells in first place, not one")
    return first[0].labels[dimensions[step.snapshot].index(step.by)]


async def patiently(fetch: Callable[[], Awaitable[QueryRetrieval]], sleep: Sleep) -> QueryRetrieval:
    """Run a fetch, waiting and trying again when the rate limit holds it back."""
    for attempt in range(PATIENCE + 1):
        try:
            return await fetch()
        except RateLimitedError as error:
            if attempt == PATIENCE:
                raise
            # anyio takes seconds as a float; time is not a value from a source.
            await sleep(float(error.retry_after or DEFAULT_WAIT))
    raise AssertionError("the loop returns or raises")


async def reference(
    question: Question, client: SsbClient, store: SnapshotStore, *, sleep: Sleep = anyio.sleep
) -> Reference:
    """Fetch a question's queries, keep them as snapshots and run its reference plan."""
    expected = question.expected
    if isinstance(expected, RefusalExpected):
        expectation = Expectation(
            question_id=question.id,
            language=question.language,
            category=question.category,
            outcome="refusal",
        )
        return Reference(expectation, (), None)
    return await answer_reference(question, expected, client, store, sleep)


async def answer_reference(
    question: Question,
    expected: AnswerExpected,
    client: SsbClient,
    store: SnapshotStore,
    sleep: Sleep,
) -> Reference:
    snapshots: dict[str, str] = {}
    dimensions: dict[str, tuple[str, ...]] = {}
    for query in expected.queries:
        retrieval = await patiently(
            lambda query=query: client.retrieve(query.table_id, query.selection, query.language),
            sleep,
        )
        await anyio.to_thread.run_sync(store.write, retrieval)
        snapshot_id = retrieval.result.provenance.response_sha256
        snapshots[query.name] = snapshot_id
        dimensions[snapshot_id] = tuple(item.code for item in retrieval.result.dimensions)
    plan = expected.checked_plan(snapshots)
    result = await anyio.to_thread.run_sync(Engine(store).run, plan)
    numbers = tuple(expected_number(result, ref) for ref in expected.numbers)
    label = None if expected.label is None else label_of(plan, result, dimensions, expected.label)
    expectation = Expectation(
        question_id=question.id,
        language=question.language,
        category=question.category,
        outcome="answer",
        numbers=numbers,
        label=label,
    )
    return Reference(expectation, tuple(snapshots.items()), result)


__all__ = ["Reference", "ReferencePlanError", "reference"]

"""One question of an evaluation run, and the report of a whole run (spec 0005).

``run_question`` runs a question's reference plan on the day's data, lets the agent
answer it through the MCP server in the same process, and scores the result. It takes
the model client from the caller, so the Inspect task in ``evals/`` can give it one
whose calls go through Inspect's agent bridge, and it imports nothing from Inspect.

A run sends no new question in the periods that ADR 0007 keeps scheduled jobs away
from, in Norwegian time, nor one whose reference plan could not run, and lists the
questions it did not run.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from datetime import time as clock
from typing import Any, Literal
from zoneinfo import ZoneInfo

import httpx2
from mcp import Client
from pydantic import Field

from opencited.agent.answer import Answer, ModelRecord, Refusal
from opencited.agent.loop import AgentError, ask
from opencited.agent.model import ModelClient
from opencited.agent.tools import Toolbox
from opencited.calc.engine import CalculationError
from opencited.evals.questions import Question
from opencited.evals.reference import ReferencePlanError, reference
from opencited.evals.report import ScoreReport, report_of
from opencited.evals.score import Failure, Verdict, score
from opencited.http.cache import ResponseCache
from opencited.http.limiter import RateLimiter
from opencited.mcp import build_server
from opencited.model import Frozen, Sha256Hex
from opencited.snapshots import SnapshotStore
from opencited.sources import SourceError
from opencited.sources.ssb import SsbClient

OSLO = ZoneInfo("Europe/Oslo")
# ADR 0007, decision 6: SSB updates metadata at 05:00 and publishes at 08:00 and
# 11:30 Norwegian time, and serves no data from 05:00 to 08:15.
AVOIDED_PERIODS = ((clock(4, 55), clock(8, 15)), (clock(11, 25), clock(11, 45)))


def in_avoided_period(moment: datetime) -> bool:
    """Return whether a moment falls in a period that runs keep away from."""
    local = moment.astimezone(OSLO).time()
    return any(start <= local < end for start, end in AVOIDED_PERIODS)


class QueryUse(Frozen):
    """A query of a reference plan, and the snapshot it was kept as."""

    name: str
    snapshot_id: Sha256Hex


class QuestionResult(Frozen):
    """What became of one question: the verdict, the agent's result and the snapshots."""

    question_id: str
    verdict: Verdict
    result: Answer | Refusal | Failure = Field(discriminator="kind")
    snapshots: tuple[QueryUse, ...]


class Skipped(Frozen):
    """A question that the run did not run, and why. ``detail`` names the error."""

    question_id: str
    reason: Literal["avoided period", "reference failed", "stopped"]
    detail: str | None = None


type Outcome = QuestionResult | Skipped


async def run_question(
    question: Question,
    *,
    model: ModelClient,
    source: SsbClient,
    shared: Shared,
    store: SnapshotStore,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    transport: httpx2.AsyncBaseTransport | None = None,
    revision_period: bool = True,
) -> Outcome:
    """Run one question: its reference plan, the agent's answer, and the verdict.

    ``transport`` and ``revision_period`` let the agent's server replay recordings, as
    ``build_server`` does.
    """
    if in_avoided_period(now()):
        return Skipped(question_id=question.id, reason="avoided period")
    try:
        expected = await reference(question, source, store)
    except (SourceError, ReferencePlanError, CalculationError) as error:
        # Without the day's expected answer the question cannot be scored, so the
        # agent is not asked it.
        return Skipped(question_id=question.id, reason="reference failed", detail=str(error))
    server = build_server(
        transport=transport,
        revision_period=revision_period,
        cache=shared.cache,
        snapshots=store,
        limiter=shared.limiter,
    )
    async with Client(server) as client:
        toolbox = await Toolbox.connect(client, store, question.language)
        try:
            result: Answer | Refusal | Failure = await ask(
                question.text, question.language, model=model, toolbox=toolbox
            )
        except AgentError as error:
            result = Failure(
                message=str(error),
                usage=error.usage,
                attempts=error.attempts,
                retries=error.retries,
            )
        except Exception as error:
            # Another error from the model's side, such as a limit of the evaluation
            # harness or a provider's own error: the question failed, and it is scored.
            result = Failure(message=f"{type(error).__name__}: {error}", usage=None)
        verdict = score(expected.expectation, result, toolbox.evidence)
    snapshots = tuple(QueryUse(name=name, snapshot_id=sid) for name, sid in expected.snapshots)
    return QuestionResult(
        question_id=question.id, verdict=verdict, result=result, snapshots=snapshots
    )


@dataclass(frozen=True)
class Shared:
    """The cache and the rate limiter that every client of a run shares.

    One limiter keeps the reference fetches and every agent's server within SSB's
    rate limit together, even when questions run at the same time, and one cache
    lets the agent read the data that the reference plan read.
    """

    cache: ResponseCache | None
    limiter: RateLimiter


class RunReport(Frozen):
    """The report of a run, committed under ``evals/results/`` when it is published."""

    question_set: str
    commit: str
    date: datetime
    model: str = Field(description="The model as Inspect named it.")
    endpoint: str | None = Field(
        description="The endpoint given for the run, or None for the provider's default."
    )
    reported_models: tuple[str, ...] = Field(
        description="The model names that the endpoint gave in its replies."
    )
    generation: dict[str, Any] = Field(description="Inspect's generation settings of the run.")
    agent: ModelRecord | None = Field(description="The agent's model settings and prices.")
    counts: ScoreReport
    questions: tuple[QuestionResult, ...]
    skipped: tuple[Skipped, ...]


def run_report(
    *,
    question_set: str,
    commit: str,
    date: datetime,
    model: str,
    endpoint: str | None,
    generation: Mapping[str, Any],
    outcomes: tuple[Outcome, ...],
    reported: tuple[str, ...] | None = None,
) -> RunReport:
    """Return the report of a run from the outcome of each question.

    ``reported`` gives the model names in the endpoint's replies when the caller read
    them elsewhere; otherwise they come from the agent's own records.
    """
    results = tuple(item for item in outcomes if isinstance(item, QuestionResult))
    records = [item.result.model for item in results if isinstance(item.result, Answer | Refusal)]
    if reported is None:
        reported = tuple(dict.fromkeys(name for record in records for name in record.reported))
    return RunReport(
        question_set=question_set,
        commit=commit,
        date=date,
        model=model,
        endpoint=endpoint,
        reported_models=reported,
        generation=dict(generation),
        agent=records[0].model_copy(update={"reported": ()}) if records else None,
        counts=report_of([item.verdict for item in results]),
        questions=results,
        skipped=tuple(item for item in outcomes if isinstance(item, Skipped)),
    )


__all__ = [
    "QueryUse",
    "QuestionResult",
    "RunReport",
    "Shared",
    "Skipped",
    "in_avoided_period",
    "run_question",
    "run_report",
]

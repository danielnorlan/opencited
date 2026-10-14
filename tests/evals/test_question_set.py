"""The first question set: its schema, its make-up, and every reference plan run in CI.

The reference plans run against the recordings in `tests/fixtures/ssb/`, which
`scripts/record_ssb_fixtures.py --questions evals/questions/v1.jsonl` made, so the
fixtures cover every table and selection the plans read (spec 0005).
"""

from __future__ import annotations

import asyncio
import re
from collections import Counter
from pathlib import Path

import pytest
from recorded import FIXTURES

from opencited.evals.questions import AnswerExpected, Question, load_questions
from opencited.evals.reference import Reference, reference
from opencited.http import make_client
from opencited.http.limiter import RateLimiter
from opencited.http.replay import replay_transport
from opencited.snapshots import SnapshotStore
from opencited.sources.ssb import BASE_URL, SsbClient

QUESTIONS = Path(__file__).resolve().parents[2] / "evals" / "questions" / "v1.jsonl"
SET = load_questions(QUESTIONS)


def test_the_first_set_has_the_questions_spec_0005_asks_for() -> None:
    categories = Counter(question.category for question in SET.questions)
    languages = Counter(question.language for question in SET.questions)

    assert SET.version == "v1"
    assert len(SET.questions) >= 40
    for category in ("lookup", "change", "share", "sum", "ranking", "false premise"):
        assert categories[category] >= 5, category
    assert categories["unanswerable"] + categories["out of scope"] >= 10
    assert languages["no"] >= 15
    assert languages["en"] >= 15


@pytest.mark.parametrize(
    "question",
    [item for item in SET.questions if isinstance(item.expected, AnswerExpected)],
    ids=lambda question: question.id,
)
def test_every_reference_plan_runs_against_the_recordings(
    question: Question, tmp_path: Path
) -> None:
    async def run() -> Reference:
        async with make_client(BASE_URL, transport=replay_transport(FIXTURES)) as http:
            client = SsbClient(http, revision_period=False)
            return await reference(question, client, SnapshotStore(tmp_path))

    result = asyncio.run(run())

    assert isinstance(question.expected, AnswerExpected)
    assert result.expectation.outcome == "answer"
    assert result.expectation.numbers
    assert len(result.snapshots) == len(question.expected.queries)


def test_every_year_in_a_question_is_a_period_its_queries_fetch() -> None:
    for question in SET.questions:
        if not isinstance(question.expected, AnswerExpected):
            continue
        periods = {
            code for query in question.expected.queries for code in query.selection.get("Tid", ())
        }
        for year in re.findall(r"(?<![0-9])(?:19|20)[0-9]{2}(?![0-9])", question.text):
            assert any(code.startswith(year) for code in periods), (question.id, year)


class Clock:
    """A clock that moves only when something sleeps."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


def test_the_whole_set_runs_on_one_client_within_ssb_s_rate_limit(tmp_path: Path) -> None:
    clock = Clock()

    async def run() -> list[Reference]:
        async with make_client(BASE_URL, transport=replay_transport(FIXTURES)) as http:
            limiter = RateLimiter(clock=clock, sleep=clock.sleep)
            client = SsbClient(http, revision_period=False, limiter=limiter)
            store = SnapshotStore(tmp_path)
            return [
                await reference(question, client, store, sleep=clock.sleep)
                for question in SET.questions
            ]

    references = asyncio.run(run())

    assert len(references) == len(SET.questions)
    # More than 30 requests went out, so the run waited on the limit.
    assert clock.now >= 60

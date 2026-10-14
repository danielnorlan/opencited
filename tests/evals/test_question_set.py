"""The question sets: their schema, their make-up, and every reference plan run in CI.

The reference plans run against the recordings in `tests/fixtures/ssb/`, which
`scripts/record_ssb_fixtures.py --questions` made for each set, so the fixtures cover
every table and selection the plans read (spec 0005).
"""

from __future__ import annotations

import asyncio
import re
from collections import Counter
from pathlib import Path

import pytest
from recorded import FIXTURES

from opencited.evals.questions import (
    AnswerExpected,
    Question,
    QuestionSet,
    RefusalExpected,
    load_questions,
)
from opencited.evals.reference import Reference, reference
from opencited.evals.score import CATEGORIES
from opencited.http import make_client
from opencited.http.limiter import RateLimiter
from opencited.http.replay import replay_transport
from opencited.snapshots import SnapshotStore
from opencited.sources.ssb import BASE_URL, SsbClient

FOLDER = Path(__file__).resolve().parents[2] / "evals" / "questions"
SET = load_questions(FOLDER / "v1.jsonl")
SECOND = load_questions(FOLDER / "v2.jsonl")
SETS = (SET, SECOND)
QUESTIONS = [question for questions in SETS for question in questions.questions]


def tables(questions: QuestionSet) -> set[str]:
    return {
        query.table_id
        for question in questions.questions
        if isinstance(question.expected, AnswerExpected)
        for query in question.expected.queries
    }


def refused(questions: QuestionSet) -> list[Question]:
    return [item for item in questions.questions if isinstance(item.expected, RefusalExpected)]


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


def test_the_second_set_asks_about_other_tables_and_says_why_it_refuses() -> None:
    categories = Counter(question.category for question in SECOND.questions)
    # The population tables that both sets read.
    population = {"06913", "11342", "05803"}

    assert SECOND.version == "v2"
    assert set(categories) == set(CATEGORIES)
    assert len(tables(SECOND) - tables(SET) - population) >= 8
    assert len(refused(SECOND)) >= len(refused(SET))
    for question in refused(SECOND):
        assert isinstance(question.expected, RefusalExpected)
        assert question.expected.reason, question.id


def test_question_ids_are_unique_across_the_sets() -> None:
    ids = [question.id for question in QUESTIONS]

    assert len(ids) == len(set(ids))


@pytest.mark.parametrize(
    "question",
    [item for item in QUESTIONS if isinstance(item.expected, AnswerExpected)],
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
    for question in QUESTIONS:
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


@pytest.mark.parametrize("questions", SETS, ids=lambda questions: questions.version)
def test_a_whole_set_runs_on_one_client_within_ssb_s_rate_limit(
    questions: QuestionSet, tmp_path: Path
) -> None:
    clock = Clock()

    async def run() -> list[Reference]:
        async with make_client(BASE_URL, transport=replay_transport(FIXTURES)) as http:
            limiter = RateLimiter(clock=clock, sleep=clock.sleep)
            client = SsbClient(http, revision_period=False, limiter=limiter)
            store = SnapshotStore(tmp_path)
            return [
                await reference(question, client, store, sleep=clock.sleep)
                for question in questions.questions
            ]

    references = asyncio.run(run())

    assert len(references) == len(questions.questions)
    # More than 30 requests went out, so the run waited on the limit.
    assert clock.now >= 60

"""The first question set: its schema, its make-up, and every reference plan run in CI.

The reference plans run against the recordings in `tests/fixtures/ssb/`, which
`scripts/record_ssb_fixtures.py --questions evals/questions/v1.jsonl` made, so the
fixtures cover every table and selection the plans read (spec 0005).
"""

from __future__ import annotations

import asyncio
from collections import Counter
from pathlib import Path

import pytest
from recorded import FIXTURES

from opencited.evals.questions import AnswerExpected, Question, load_questions
from opencited.evals.reference import Reference, reference
from opencited.http import make_client
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

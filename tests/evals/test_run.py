"""One question of an evaluation run, without a model, and the report of a run.

SSB's responses come from the recordings. The model endpoint cannot be reached, so a
question's run ends in a failure; no test here gets an answer from a model.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx2
import pytest
from recorded import FIXTURES

from opencited.agent.config import ModelConfig
from opencited.agent.model import ModelClient
from opencited.evals.questions import load_questions
from opencited.evals.run import (
    Outcome,
    QueryUse,
    QuestionResult,
    Shared,
    Skipped,
    in_avoided_period,
    run_question,
    run_report,
)
from opencited.http import make_client
from opencited.http.limiter import RateLimiter
from opencited.http.replay import replay_transport
from opencited.snapshots import SnapshotStore
from opencited.sources.ssb import BASE_URL, SsbClient

QUESTIONS = load_questions(Path(__file__).resolve().parents[2] / "evals" / "questions" / "v1.jsonl")
BY_ID = {question.id: question for question in QUESTIONS.questions}
KEY = "key-for-the-run-test"
CONFIG = ModelConfig(
    base_url="https://models.example.org/v1", model="a-model", key_variable="A_KEY"
)
# 10:00 in Oslo on a weekday, outside the periods that runs avoid.
OPEN = datetime(2026, 10, 14, 8, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("hour", "minute", "avoided"),
    [
        (4, 54, False),
        (4, 55, True),
        (8, 14, True),
        (8, 15, False),
        (11, 24, False),
        (11, 25, True),
        (11, 44, True),
        (11, 45, False),
    ],
)
def test_runs_keep_away_from_ssb_s_update_periods_in_norwegian_time(
    hour: int, minute: int, avoided: bool
) -> None:
    # October in Oslo is two hours ahead of UTC.
    moment = datetime(2026, 10, 14, hour - 2, minute, tzinfo=UTC)

    assert in_avoided_period(moment) is avoided


class Recorder:
    """Replays the recordings and keeps the paths it was asked for."""

    def __init__(self) -> None:
        self.paths: list[str] = []
        self.transport = httpx2.MockTransport(self.handle)

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.paths.append(request.url.path)
        return replay_transport(FIXTURES).handle_request(request)


def unreachable(request: httpx2.Request) -> httpx2.Response:
    raise httpx2.ConnectError("the endpoint is unreachable in tests", request=request)


def run(question_id: str, tmp_path: Path, moment: datetime) -> tuple[Outcome, list[str]]:
    recorder = Recorder()
    store = SnapshotStore(tmp_path)

    async def work() -> Outcome:
        async with (
            make_client(BASE_URL, transport=recorder.transport) as http,
            ModelClient(
                CONFIG, environ={"A_KEY": KEY}, transport=httpx2.MockTransport(unreachable)
            ) as model,
        ):
            limiter = RateLimiter()
            source = SsbClient(http, revision_period=False, limiter=limiter)
            return await run_question(
                BY_ID[question_id],
                model=model,
                source=source,
                shared=Shared(cache=None, limiter=limiter),
                store=store,
                now=lambda: moment,
                transport=recorder.transport,
                revision_period=False,
            )

    return asyncio.run(work()), recorder.paths


def test_a_question_in_an_avoided_period_is_not_run(tmp_path: Path) -> None:
    outcome, paths = run(
        "lookup-population-norway-2026", tmp_path, datetime(2026, 10, 14, 4, 0, tzinfo=UTC)
    )

    assert outcome == Skipped(question_id="lookup-population-norway-2026", reason="avoided period")
    assert paths == []


def test_a_run_whose_model_cannot_be_reached_is_a_failure_with_its_snapshots(
    tmp_path: Path,
) -> None:
    outcome, _ = run("lookup-population-norway-2026", tmp_path, OPEN)

    assert isinstance(outcome, QuestionResult)
    verdict = outcome.verdict
    assert (verdict.outcome, verdict.correct, verdict.expected) == ("failure", False, "answer")
    assert verdict.reason == "the endpoint could not be reached: Connection error."
    [use] = outcome.snapshots
    assert use.name == "Q1"
    assert SnapshotStore(tmp_path).read(use.snapshot_id).manifest.snapshot_id == use.snapshot_id


def test_a_question_that_should_be_refused_fetches_nothing_before_the_agent(tmp_path: Path) -> None:
    outcome, paths = run("out-of-scope-weather-oslo", tmp_path, OPEN)

    assert isinstance(outcome, QuestionResult)
    assert outcome.snapshots == ()
    assert outcome.verdict.expected == "refusal"
    assert all(not path.endswith("/data") for path in paths)


def test_the_run_report_holds_every_result_the_counts_and_no_key(tmp_path: Path) -> None:
    failed, _ = run("lookup-population-norway-2026", tmp_path / "a", OPEN)
    refused, _ = run("out-of-scope-weather-oslo", tmp_path / "b", OPEN)
    skipped = Skipped(question_id="sum-largest-cities-2026", reason="avoided period")

    report = run_report(
        question_set="v1",
        commit="0123456789abcdef0123456789abcdef01234567",
        date=OPEN,
        model="provider/a-model",
        endpoint="https://models.example.org/v1",
        generation={"temperature": 0},
        outcomes=(failed, skipped, refused),
    )

    assert [item.question_id for item in report.questions] == [
        "lookup-population-norway-2026",
        "out-of-scope-weather-oslo",
    ]
    assert report.skipped == (skipped,)
    assert report.counts.questions == 2
    assert report.counts.failures == 2
    assert report.agent is None
    text = report.model_dump_json()
    assert KEY not in text
    assert "A_KEY" not in text
    assert type(report).model_validate_json(text) == report
    assert json.loads(text)["questions"][0]["result"]["kind"] == "failure"
    assert isinstance(report.questions[0].snapshots[0], QueryUse)

"""The Inspect task without a model: its samples, limits, scores and run report.

These tests need the `evals` dependency group. The outcomes they score come from a
run whose model endpoint could not be reached, so no answer of a model is made up.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx2
import pytest
from inspect_ai.log import EvalConfig, EvalDataset, EvalLog, EvalRevision, EvalSample, EvalSpec
from inspect_ai.scorer import CORRECT, INCORRECT, NOANSWER
from opencited_task import (
    OUTCOME,
    QUESTIONS,
    bridge_config,
    kept,
    opencited,
    samples,
    score_of,
)
from recorded import FIXTURES
from report import file_name, main, report_of_log

from opencited.agent.config import Limits, Prices
from opencited.agent.model import ModelClient
from opencited.evals.questions import load_questions
from opencited.evals.run import Outcome, QuestionResult, Shared, Skipped, run_question
from opencited.http import make_client
from opencited.http.limiter import RateLimiter
from opencited.http.replay import replay_transport
from opencited.snapshots import SnapshotStore
from opencited.sources.ssb import BASE_URL, SsbClient

SET = load_questions(QUESTIONS)
OPEN = datetime(2026, 10, 14, 8, 0, tzinfo=UTC)


def failed_outcome(tmp_path: Path, question_id: str) -> Outcome:
    """Run a question whose model endpoint cannot be reached, on the recordings."""

    def unreachable(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("the endpoint is unreachable in tests", request=request)

    config = bridge_config(temperature=None, seed=None, prices=None, limits=Limits())
    replay = replay_transport(FIXTURES)
    question = next(item for item in SET.questions if item.id == question_id)

    async def work() -> Outcome:
        async with (
            make_client(BASE_URL, transport=replay) as http,
            ModelClient(
                config,
                environ={"OPENCITED_BRIDGE_KEY": "x"},
                transport=httpx2.MockTransport(unreachable),
            ) as model,
        ):
            limiter = RateLimiter()
            return await run_question(
                question,
                model=model,
                source=SsbClient(http, revision_period=False, limiter=limiter),
                shared=Shared(cache=None, limiter=limiter),
                store=SnapshotStore(tmp_path),
                now=lambda: OPEN,
                transport=replay,
                revision_period=False,
            )

    return asyncio.run(work())


def test_each_question_is_a_sample_with_its_id_text_and_category() -> None:
    found = samples(SET)

    assert [sample.id for sample in found] == [question.id for question in SET.questions]
    assert [sample.input for sample in found] == [question.text for question in SET.questions]
    first = found[0].metadata or {}
    assert first == {
        "question_set": "v1",
        "language": SET.questions[0].language,
        "category": SET.questions[0].category,
        "expected": SET.questions[0].expected.outcome,
    }


def test_inspect_s_limits_lie_above_the_agent_s(tmp_path: Path) -> None:
    task = opencited(
        run_dir=str(tmp_path), price_input="0.28", price_output="0.42", max_cost="0.05"
    )
    plain = opencited(run_dir=str(tmp_path))

    assert (task.token_limit, task.time_limit, task.cost_limit) == (150_000, 900, 0.075)
    assert plain.cost_limit is None
    assert len(task.dataset) == len(SET.questions)


def test_the_agent_reaches_the_model_through_the_bridge_with_its_settings() -> None:
    prices = Prices(input=Decimal("0.28"), output=Decimal("0.42"))

    config = bridge_config(temperature="0.2", seed=7, prices=prices, limits=Limits())

    assert (config.model, config.key_variable) == ("inspect", "OPENCITED_BRIDGE_KEY")
    assert config.settings.temperature == Decimal("0.2")
    assert (config.settings.seed, config.prices) == (7, prices)


def test_a_verdict_becomes_inspect_s_score(tmp_path: Path) -> None:
    outcome = failed_outcome(tmp_path, "lookup-population-norway-2026")
    assert isinstance(outcome, QuestionResult)

    score = score_of(outcome)
    skipped = score_of(Skipped(question_id="sum-largest-cities-2026", reason="avoided period"))

    assert score.value == INCORRECT
    assert score.metadata == {"verdict": outcome.verdict.model_dump(mode="json")}
    assert skipped.value == NOANSWER
    correct = outcome.model_copy(
        update={"verdict": outcome.verdict.model_copy(update={"correct": True})}
    )
    assert score_of(correct).value == CORRECT


def log_of(outcomes: list[Outcome]) -> EvalLog:
    """An Inspect log that holds the outcomes as the solver keeps them."""
    spec = EvalSpec(
        created="2026-10-14T10:00:00+02:00",
        task="opencited",
        dataset=EvalDataset(name="v1"),
        model="provider/a-model",
        model_base_url="https://models.example.org/v1",
        config=EvalConfig(),
        revision=EvalRevision(
            type="git",
            origin="https://github.com/danielnorlan/opencited",
            commit="0123456",
            dirty=False,
        ),
        metadata={"question_set": "v1"},
    )
    entries = [
        EvalSample(
            id=item.question_id,
            epoch=1,
            input="",
            target="",
            store={OUTCOME: kept(item)},
        )
        for item in outcomes
    ]
    return EvalLog(eval=spec, samples=entries)


def test_the_run_report_comes_from_the_log(tmp_path: Path) -> None:
    failed = failed_outcome(tmp_path / "a", "lookup-population-norway-2026")
    skipped = Skipped(question_id="sum-largest-cities-2026", reason="avoided period")

    report = report_of_log(log_of([failed, skipped]))

    assert (report.question_set, report.commit, report.model) == (
        "v1",
        "0123456",
        "provider/a-model",
    )
    assert report.endpoint == "https://models.example.org/v1"
    assert report.date == datetime(2026, 10, 14, 8, 0, tzinfo=UTC)
    assert report.skipped == (skipped,)
    assert [item.question_id for item in report.questions] == ["lookup-population-norway-2026"]
    assert file_name(report) == "2026-10-14-v1-provider-a-model.json"


def test_a_log_without_an_outcome_or_a_commit_is_refused(tmp_path: Path) -> None:
    log = log_of([])
    log.samples = [EvalSample(id="q", epoch=1, input="", target="", store={})]
    with pytest.raises(ValueError, match="holds no outcome"):
        report_of_log(log)
    log.eval.revision = None
    with pytest.raises(ValueError, match="names no commit"):
        report_of_log(log)


def test_the_report_is_written_as_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log = log_of([Skipped(question_id="sum-largest-cities-2026", reason="avoided period")])

    def read(path: str) -> EvalLog:
        return log

    monkeypatch.setattr("report.read_eval_log", read)

    assert main(["run.eval", "--out", str(tmp_path)]) == 0
    written = tmp_path / "2026-10-14-v1-provider-a-model.json"
    assert written.read_text().startswith("{")

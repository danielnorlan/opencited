"""The Inspect task without a model: its samples, limits, scores and run report.

These tests need the `evals` dependency group. The outcomes they score come from a
run whose model endpoint could not be reached, so no answer of a model is made up.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import httpx2
import pytest
from inspect_ai.event import ModelEvent
from inspect_ai.log import EvalConfig, EvalDataset, EvalLog, EvalRevision, EvalSample, EvalSpec
from inspect_ai.model import GenerateConfig, ModelOutput
from inspect_ai.scorer import CORRECT, INCORRECT, NOANSWER
from inspect_ai.tool import ToolParams
from opencited_task import (
    OUTCOME,
    QUESTIONS,
    bridge_config,
    decimal_of,
    kept,
    opencited,
    samples,
    score_of,
)
from recorded import FIXTURES
from report import ReportError, file_name, main, report_of_log

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
    # Inspect's YAML parser gives -T values as numbers.
    task = opencited(price_input=0.28, price_output=0.42, max_cost=0.05, run_dir=str(tmp_path))

    assert (task.token_limit, task.time_limit, task.cost_limit) == (150_000, 900, None)
    assert task.fail_on_error is False
    assert len(task.dataset) == len(SET.questions)
    [folder] = list(tmp_path.iterdir())
    assert (folder / "snapshots").is_dir()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.28, Decimal("0.28")),
        (0.05, Decimal("0.05")),
        (0, Decimal(0)),
        (7, Decimal(7)),
        ("0.1", Decimal("0.1")),
    ],
)
def test_task_arguments_become_the_decimals_written(
    value: float | int | str, expected: Decimal
) -> None:
    assert decimal_of("price", value) == expected
    assert str(decimal_of("price", value)) == str(expected)


@pytest.mark.parametrize("value", [True, "cheap"])
def test_a_task_argument_that_is_no_number_is_refused(value: bool | str) -> None:
    with pytest.raises(ValueError, match="price must be a number"):
        decimal_of("price", value)


def test_the_agent_reaches_the_model_through_the_bridge_with_its_settings() -> None:
    prices = Prices(input=Decimal("0.28"), output=Decimal("0.42"))

    config = bridge_config(temperature=0.2, seed=7, prices=prices, limits=Limits())
    zero = bridge_config(temperature=0, seed=None, prices=prices, limits=Limits())

    assert (config.model, config.key_variable) == ("inspect", "OPENCITED_BRIDGE_KEY")
    assert config.settings.temperature == Decimal("0.2")
    assert (config.settings.seed, config.prices) == (7, prices)
    assert zero.settings.temperature == Decimal(0)


def test_the_tools_keep_their_schemas_through_the_bridge(tmp_path: Path) -> None:
    from mcp import Client

    from opencited.agent.model import tool_param
    from opencited.agent.tools import Toolbox
    from opencited.mcp import build_server

    async def schemas() -> list[dict[str, object]]:
        server = build_server(transport=replay_transport(FIXTURES), revision_period=False)
        async with Client(server) as client:
            toolbox = await Toolbox.connect(client, SnapshotStore(tmp_path), "no")
            return [dict(tool_param(schema)["function"]) for schema in toolbox.schemas]

    for function in asyncio.run(schemas()):
        parameters = cast("dict[str, Any]", function["parameters"])
        kept = ToolParams.model_validate(parameters).model_dump(mode="json", exclude_none=True)
        # Inspect refuses other arguments where the schema is silent, as the agent does.
        if "additionalProperties" not in parameters:
            assert kept.pop("additionalProperties") is False
        # Every keyword the agent sends survives Inspect's tool parameters, apart from
        # the limits on a list's length and the plan's own description, which the tool's
        # description repeats and the agent checks.
        sent = cast("dict[str, Any]", without(parameters, LOST))
        sent.pop("description", None)
        assert normalised(kept) == normalised(sent), function["name"]


LOST = frozenset({"minItems", "maxItems"})


def without(schema: object, keys: frozenset[str]) -> object:
    if isinstance(schema, dict):
        items = cast("dict[str, object]", schema).items()
        return {key: without(value, keys) for key, value in items if key not in keys}
    if isinstance(schema, list):
        return [without(item, keys) for item in cast("list[object]", schema)]
    return schema


def normalised(schema: object) -> object:
    """A schema without the empty defaults that Inspect's model writes out."""
    if isinstance(schema, dict):
        items = cast("dict[str, object]", schema).items()
        return {key: normalised(value) for key, value in items if value not in ({}, [], None)}
    if isinstance(schema, list):
        return [normalised(item) for item in cast("list[object]", schema)]
    return schema


def test_a_verdict_becomes_inspect_s_score(tmp_path: Path) -> None:
    outcome = failed_outcome(tmp_path, "lookup-population-norway-2026")
    assert isinstance(outcome, QuestionResult)
    skipped = Skipped(question_id="sum-largest-cities-2026", reason="avoided period")

    score = score_of(outcome)

    assert score.value == INCORRECT
    assert score.metadata == {"verdict": outcome.verdict.model_dump(mode="json")}
    assert score_of(skipped).value == NOANSWER
    assert score_of(skipped).metadata == {"skipped": skipped.model_dump(mode="json")}
    correct = outcome.model_copy(
        update={"verdict": outcome.verdict.model_copy(update={"correct": True})}
    )
    assert score_of(correct).value == CORRECT


def log_of(outcomes: list[Outcome], models: tuple[str, ...] = ()) -> EvalLog:
    """An Inspect log that holds the outcomes as the solver keeps them."""
    spec = EvalSpec(
        created="2026-10-14T10:00:00+02:00",
        task="opencited",
        dataset=EvalDataset(name="v1"),
        model="provider/a-model",
        model_base_url="https://models.example.org/v1",
        model_generate_config=GenerateConfig(
            temperature=0.2, extra_headers={"Authorization": "Bearer abc"}
        ),
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
            # Only the model name of a reply, as the endpoint gave it.
            events=[
                ModelEvent(
                    model="provider/a-model",
                    input=[],
                    tools=[],
                    tool_choice="auto",
                    config=GenerateConfig(),
                    output=ModelOutput(model=name),
                )
                for name in models
            ],
        )
        for item in outcomes
    ]
    return EvalLog(eval=spec, samples=entries, status="success")


def test_the_run_report_comes_from_the_log(tmp_path: Path) -> None:
    failed = failed_outcome(tmp_path / "a", "lookup-population-norway-2026")
    skipped = Skipped(question_id="sum-largest-cities-2026", reason="avoided period")

    report = report_of_log(log_of([failed, skipped], models=("a-model-2026-10-01",)))

    assert (report.question_set, report.commit, report.model) == (
        "v1",
        "0123456",
        "provider/a-model",
    )
    assert report.endpoint == "https://models.example.org/v1"
    assert report.reported_models == ("a-model-2026-10-01",)
    assert report.generation == {"temperature": 0.2}
    assert report.date == datetime(2026, 10, 14, 8, 0, tzinfo=UTC)
    assert [item.question_id for item in report.questions] == ["lookup-population-norway-2026"]
    stopped = [item for item in report.skipped if item.reason == "stopped"]
    assert len(stopped) == len(SET.questions) - 2
    assert {item.detail for item in stopped} == {"no sample in the log"}
    assert skipped in report.skipped
    assert file_name(report) == "2026-10-14T0800-v1-provider-a-model.json"


def test_a_sample_that_kept_no_outcome_is_listed_as_stopped() -> None:
    log = log_of([])
    log.samples = [
        EvalSample(id="lookup-population-norway-2026", epoch=1, input="", target="", store={})
    ]

    report = report_of_log(log)

    assert report.skipped[0] == Skipped(
        question_id="lookup-population-norway-2026", reason="stopped", detail="no outcome kept"
    )


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ("status", "the run did not finish"),
        ("revision", "names no commit"),
        ("epoch", "more than one epoch"),
        ("sample", "samples outside the set v1"),
        ("endpoint", "the endpoint cannot go in a report"),
    ],
)
def test_a_log_that_cannot_give_a_complete_report_is_refused(change: str, message: str) -> None:
    log = log_of([Skipped(question_id="sum-largest-cities-2026", reason="avoided period")])
    samples = log.samples or []
    if change == "status":
        log.status = "cancelled"
    elif change == "revision":
        log.eval.revision = None
    elif change == "epoch":
        samples[0].epoch = 2
    elif change == "sample":
        samples[0].id = "a-question-of-another-set"
    else:
        log.eval.model_base_url = "https://models.example.org/v1?key=abc"

    with pytest.raises(ReportError, match=message):
        report_of_log(log)


def written_with(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, log: EvalLog, environ: dict[str, str]
) -> int:
    def read(path: str) -> EvalLog:
        return log

    monkeypatch.setattr("report.read_eval_log", read)
    return main(["run.eval", "--out", str(tmp_path)], environ=environ)


def test_the_report_is_written_once_as_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    log = log_of([Skipped(question_id="sum-largest-cities-2026", reason="avoided period")])

    assert written_with(tmp_path, monkeypatch, log, {}) == 0
    written = tmp_path / "2026-10-14T0800-v1-provider-a-model.json"
    assert written.read_text().startswith("{")
    assert written_with(tmp_path, monkeypatch, log, {}) == 1
    assert "is not replaced" in capsys.readouterr().err


def test_a_report_that_holds_a_secret_is_not_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    log = log_of([Skipped(question_id="sum-largest-cities-2026", reason="avoided period")])
    log.eval.model = "provider/model-secret-12345"

    code = written_with(
        tmp_path,
        monkeypatch,
        log,
        {"PROVIDER_API_KEY": "model-secret-12345", "PROVIDER_REGION": "model-secret"},
    )

    assert code == 1
    assert "holds the value of PROVIDER_API_KEY" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []

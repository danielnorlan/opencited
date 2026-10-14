"""The loop without a model: an endpoint that cannot be reached, and the time limit.

What the loop does with a model is tested only against a real endpoint, in
`tests/live/test_model.py`.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterator
from decimal import Decimal
from pathlib import Path

import httpx2
import pytest
from answering import CONFIG
from mcp import Client
from recorded import FIXTURES

from opencited.agent.answer import Answer, ModelRecord, Refusal, RunUsage
from opencited.agent.config import Limits, ModelConfig
from opencited.agent.loop import AgentError, Run, ask
from opencited.agent.model import Completion, Message, ModelClient, Usage, UserMessage
from opencited.agent.prompt import retry_message, uncited_message
from opencited.agent.run import RunState
from opencited.agent.tools import Toolbox
from opencited.gate import Report, check
from opencited.http.replay import replay_transport
from opencited.mcp import build_server
from opencited.snapshots import SnapshotStore

KEY = "key-for-the-loop-test"


class Unreachable:
    """A transport that keeps each request and fails to connect."""

    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []
        self.transport = httpx2.MockTransport(self.handle)

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        raise httpx2.ConnectError("the endpoint is unreachable in tests", request=request)


def ticks(*seconds: int) -> Iterator[int]:
    """Yield the given times in nanoseconds, then the last one again and again."""
    return nanoseconds(*(second * 1_000_000_000 for second in seconds))


def nanoseconds(*moments: int) -> Iterator[int]:
    yield from moments
    while True:
        yield moments[-1]


def run(
    tmp_path: Path, config: ModelConfig, endpoint: Unreachable, clock: Iterator[int]
) -> Answer | Refusal:
    store = SnapshotStore(tmp_path)
    server = build_server(
        transport=replay_transport(FIXTURES), revision_period=False, snapshots=store
    )

    async def work() -> Answer | Refusal | AgentError:
        async with (
            Client(server) as client,
            ModelClient(config, environ={"A_KEY": KEY}, transport=endpoint.transport) as model,
        ):
            toolbox = await Toolbox.connect(client, store, "no")
            # Raised inside the client's task group, the error would leave in a group.
            try:
                return await ask(
                    "Hvor mange bor i Norge?",
                    "no",
                    model=model,
                    toolbox=toolbox,
                    clock=lambda: next(clock),
                )
            except AgentError as error:
                return error

    result = asyncio.run(work())
    if isinstance(result, AgentError):
        raise result
    return result


def test_an_endpoint_that_cannot_be_reached_fails_the_run_with_its_usage(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    endpoint = Unreachable()

    with pytest.raises(AgentError) as caught:
        run(tmp_path, CONFIG, endpoint, ticks(0, 2))

    assert str(caught.value) == "the endpoint could not be reached: Connection error."
    assert caught.value.usage == RunUsage(
        model_calls=0,
        tool_calls=0,
        input_tokens=0,
        output_tokens=0,
        seconds=Decimal("2.000"),
        cost=Decimal(0),
    )
    [request] = endpoint.requests
    assert KEY not in caplog.text
    assert KEY not in str(caught.value)
    # The first request holds the prompt, the question and the four tools.
    body = request.content.decode()
    assert '"Hvor mange bor i Norge?"' in body
    assert "Write the answer in Norwegian (bokm" in body
    for name in ("search_tables", "get_table_metadata", "query_table", "calculate"):
        assert f'"name":"{name}"' in body


def test_a_run_that_has_used_its_time_refuses_before_the_next_call(tmp_path: Path) -> None:
    endpoint = Unreachable()
    config = CONFIG.model_copy(update={"limits": Limits(seconds=60)})

    result = run(tmp_path, config, endpoint, ticks(0, 60))

    assert endpoint.requests == []
    assert result == Refusal(
        question="Hvor mange bor i Norge?",
        language="no",
        reason="limit",
        detail="the time limit of 60 seconds",
        limit="time",
        explanation=None,
        report=None,
        model=ModelRecord(
            base_url=CONFIG.base_url,
            model=CONFIG.model,
            reported=(),
            settings=CONFIG.settings,
            prices=CONFIG.prices,
        ),
        prompt_version="1",
        usage=RunUsage(
            model_calls=0,
            tool_calls=0,
            input_tokens=0,
            output_tokens=0,
            seconds=Decimal("60.000"),
            cost=Decimal(0),
        ),
    )


def test_the_run_time_is_kept_to_the_millisecond(tmp_path: Path) -> None:
    with pytest.raises(AgentError) as caught:
        run(tmp_path, CONFIG, Unreachable(), nanoseconds(0, 1_234_567_890))

    assert caught.value.usage is not None
    assert caught.value.usage.seconds == Decimal("1.235")


class Silent:
    """A transport that keeps each request and gives no response within the test."""

    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []
        self.transport = httpx2.MockTransport(self.handle)

    async def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        await asyncio.sleep(30)
        raise AssertionError("the run should have stopped the call")


def test_a_model_call_past_the_time_limit_is_cancelled_and_refused(tmp_path: Path) -> None:
    endpoint = Silent()
    config = CONFIG.model_copy(update={"limits": Limits(seconds=1)})
    store = SnapshotStore(tmp_path)
    server = build_server(
        transport=replay_transport(FIXTURES), revision_period=False, snapshots=store
    )

    async def work() -> Answer | Refusal:
        async with (
            Client(server) as client,
            ModelClient(config, environ={"A_KEY": KEY}, transport=endpoint.transport) as model,
        ):
            toolbox = await Toolbox.connect(client, store, "no")
            return await ask("Hvor mange bor i Norge?", "no", model=model, toolbox=toolbox)

    result = asyncio.run(work())

    assert len(endpoint.requests) == 1
    assert isinstance(result, Refusal)
    assert (result.reason, result.limit, result.usage.model_calls) == ("limit", "time", 0)
    assert result.usage.seconds >= 1


def with_run[T](tmp_path: Path, work: Callable[[Run], Awaitable[T]]) -> T:
    """Run ``work`` on a run whose model is never reached and whose tools are real."""
    store = SnapshotStore(tmp_path)
    server = build_server(
        transport=replay_transport(FIXTURES), revision_period=False, snapshots=store
    )

    async def go() -> T:
        async with (
            Client(server) as client,
            ModelClient(CONFIG, environ={"A_KEY": KEY}, transport=Unreachable().transport) as model,
        ):
            toolbox = await Toolbox.connect(client, store, "no")
            clock = ticks(0)
            return await work(
                Run("Hvor mange bor i Norge?", "no", model, toolbox, lambda: next(clock))
            )

    return asyncio.run(go())


USAGE = Usage(1_200, 80)


def reply(text: str, usage: Usage = USAGE) -> Completion:
    return Completion(text, (), "stop", "a-model", usage)


@pytest.mark.parametrize(
    ("explanation", "kept"),
    [
        ("Statistikkbanken har ingen tabell over dette.", True),
        ("Statistikkbanken har bare tall for 12 kommuner fra 2031.", False),
    ],
)
def test_a_refusal_keeps_the_model_s_explanation_only_if_the_gate_passes_it(
    tmp_path: Path, explanation: str, kept: bool
) -> None:
    async def work(run: Run) -> Answer | Refusal:
        run.state = RunState().replied(reply(f"REFUSE: no data\n{explanation}"))
        return await run.answer()

    result = with_run(tmp_path, work)

    assert isinstance(result, Refusal)
    assert (result.reason, result.detail) == ("no data", "the model refused")
    assert result.explanation == (explanation if kept else None)


def blocked_and_passed() -> tuple[Report, Report]:
    return check("Det bodde 12 personer der.", "no", []), check("Ingen tall her.", "no", [])


@pytest.mark.parametrize("last", ["blocked", "passed"])
def test_a_refusal_holds_the_last_report_only_after_a_blocked_answer(
    tmp_path: Path, last: str
) -> None:
    blocked, passed = blocked_and_passed()
    report = blocked if last == "blocked" else passed

    async def work(run: Run) -> Answer | Refusal:
        spent = reply("Svar.", Usage(99_000, 1_000))
        run.state = RunState().replied(spent).checked(report, ("E1",))
        return await run.answer()

    result = with_run(tmp_path, work)

    assert isinstance(result, Refusal)
    assert (result.reason, result.limit) == ("limit", "tokens")
    assert result.report == (blocked if last == "blocked" else None)


@pytest.mark.parametrize("cited", [(), ("E1",)])
def test_a_retry_asks_to_cite_or_sends_the_gate_report(
    tmp_path: Path, cited: tuple[str, ...]
) -> None:
    blocked, passed = blocked_and_passed()
    report = passed if not cited else blocked

    async def work(run: Run) -> Message:
        run.state = RunState().replied(reply("Svar.")).checked(report, cited)
        # The retry is sent; the next model call then fails on the unreachable endpoint.
        with pytest.raises(AgentError):
            await run.answer()
        return run.messages[-1]

    message = with_run(tmp_path, work)

    expected = uncited_message() if not cited else retry_message(blocked)
    assert message == UserMessage(expected)

"""The loop without a model: an endpoint that cannot be reached, and the time limit.

What the loop does with a model is tested only against a real endpoint, in
`tests/live/test_model.py`.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import httpx2
import pytest
from answering import CONFIG
from mcp import Client
from recorded import FIXTURES

from opencited.agent.answer import Answer, ModelRecord, Refusal, RunUsage
from opencited.agent.config import Limits, ModelConfig
from opencited.agent.loop import AgentError, ask
from opencited.agent.model import ModelClient
from opencited.agent.tools import Toolbox
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
    yield from (second * 1_000_000_000 for second in seconds)
    while True:
        yield seconds[-1] * 1_000_000_000


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

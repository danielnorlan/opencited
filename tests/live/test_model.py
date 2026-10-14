"""The model client against the real endpoint that the OPENCITED_MODEL_* variables name.

The tests are marked `model` and run only with `pytest -m model`, since each call
needs a key and costs a little. The default run and the nightly workflow leave them
out, and without the variables they fail and name the one that is missing. They check
that the endpoint takes the four tool schemas and reports a model name and usage, and
that the agent answers or refuses a question against SSB's live API, with an answer
that passes the gate again. Whether the model answers well is for the evaluation
suite (spec 0005).
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from mcp import Client
from recorded import FIXTURES

from opencited.agent.answer import Answer, Refusal
from opencited.agent.config import from_environment
from opencited.agent.loop import AgentError, ask
from opencited.agent.model import (
    Completion,
    Message,
    ModelClient,
    SystemMessage,
    ToolSchema,
    UserMessage,
)
from opencited.agent.tools import Toolbox
from opencited.gate import Evidence, check
from opencited.http.replay import replay_transport
from opencited.mcp import build_server
from opencited.snapshots import SnapshotStore

pytestmark = pytest.mark.model


def complete(messages: list[Message], tools: tuple[ToolSchema, ...]) -> Completion:
    config = from_environment(os.environ)

    async def run() -> Completion:
        async with ModelClient(config) as client:
            return await client.complete(messages, tools)

    return asyncio.run(run())


def schemas(tmp_path: Path) -> tuple[ToolSchema, ...]:
    async def run() -> tuple[ToolSchema, ...]:
        server = build_server(transport=replay_transport(FIXTURES), revision_period=False)
        async with Client(server) as client:
            toolbox = await Toolbox.connect(client, SnapshotStore(tmp_path), "en")
            return toolbox.schemas

    return asyncio.run(run())


def test_the_endpoint_answers_and_reports_the_model_and_usage() -> None:
    completion = complete(
        [SystemMessage("Reply with one word."), UserMessage("Say the word ready.")], ()
    )

    assert completion.model
    assert completion.usage is not None
    assert completion.usage.input_tokens > 0
    assert completion.text or completion.tool_calls


def test_the_endpoint_takes_the_four_tool_schemas(tmp_path: Path) -> None:
    tools = schemas(tmp_path)

    completion = complete(
        [
            SystemMessage("Answer only from the tools."),
            UserMessage("Which StatBank tables hold the population of Norway?"),
        ],
        tools,
    )

    assert completion.usage is not None
    assert {call.name for call in completion.tool_calls} <= {tool.name for tool in tools}


def test_the_agent_answers_or_refuses_and_an_answer_passes_the_gate_again(
    tmp_path: Path,
) -> None:
    config = from_environment(os.environ)
    store = SnapshotStore(tmp_path)

    async def run() -> tuple[Answer | Refusal | AgentError, tuple[Evidence, ...]]:
        async with (
            Client(build_server(snapshots=store)) as client,
            ModelClient(config) as model,
        ):
            toolbox = await Toolbox.connect(client, store, "no")
            try:
                result = await ask("Hvor mange bor i Norge?", "no", model=model, toolbox=toolbox)
            except AgentError as error:
                return error, toolbox.evidence
            return result, toolbox.evidence

    result, evidence = asyncio.run(run())

    assert not isinstance(result, AgentError), str(result)
    assert result.model.reported
    if isinstance(result, Answer):
        assert check(result.text, "no", evidence).passed

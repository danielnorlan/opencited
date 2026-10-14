"""What the model gets of the conversation once search and metadata results are spent (#51).

The tool results come from the recordings through the toolbox, as in a run.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from mcp import Client
from recorded import FIXTURES

from opencited.agent.history import SourceMessage, spent, visible
from opencited.agent.model import (
    AssistantMessage,
    Message,
    SystemMessage,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from opencited.agent.prompt import SPENT_NOTES
from opencited.agent.tools import Toolbox, ToolOutcome
from opencited.gate import Evidence, SourceEvidence, check
from opencited.http.replay import replay_transport
from opencited.mcp import build_server
from opencited.snapshots import SnapshotStore

SEARCH = ("search_tables", {"query": "befolkning", "language": "no"})
METADATA = ("get_table_metadata", {"table_id": "06913", "language": "no"})
OTHER_METADATA = ("get_table_metadata", {"table_id": "05810", "language": "no"})
QUERY = (
    "query_table",
    {
        "table_id": "06913",
        "selection": {"Region": ["0"], "ContentsCode": ["Folkemengde"], "Tid": ["2025", "2026"]},
        "language": "no",
    },
)


def conversation(
    tmp_path: Path, *calls: tuple[str, dict[str, Any]]
) -> tuple[list[Message], list[SourceMessage], tuple[Evidence, ...]]:
    """Run the calls one by one, as a model would ask for them, and keep the conversation."""

    async def work() -> tuple[list[Message], list[SourceMessage], tuple[Evidence, ...]]:
        server = build_server(transport=replay_transport(FIXTURES), revision_period=False)
        async with Client(server) as client:
            toolbox = await Toolbox.connect(client, SnapshotStore(tmp_path), "no")
            messages: list[Message] = [SystemMessage("prompt"), UserMessage("question")]
            sources: list[SourceMessage] = []
            for number, (name, arguments) in enumerate(calls):
                call = ToolCall(f"call_{number}", name, json.dumps(arguments))
                messages.append(AssistantMessage(None, (call,)))
                outcome: ToolOutcome = await toolbox.run(call)
                assert outcome.status == "result", outcome.text
                messages.append(ToolMessage(call.call_id, outcome.text))
                if isinstance(outcome.evidence, SourceEvidence):
                    sources.append(SourceMessage(len(messages) - 1, outcome.evidence))
            return messages, sources, toolbox.evidence

    return asyncio.run(work())


def texts(messages: list[Message]) -> list[str]:
    return [message.text for message in messages if isinstance(message, ToolMessage)]


def test_nothing_is_spent_before_the_model_has_chosen_a_table(tmp_path: Path) -> None:
    messages, sources, _ = conversation(tmp_path, SEARCH)

    assert visible(messages, sources) == messages


def test_metadata_alone_spends_nothing(tmp_path: Path) -> None:
    # The table may be the wrong one, and then the model needs the search again.
    messages, sources, _ = conversation(tmp_path, SEARCH, OTHER_METADATA, METADATA)

    assert visible(messages, sources) == messages


def test_the_metadata_is_spent_once_its_table_was_queried(tmp_path: Path) -> None:
    messages, sources, _ = conversation(tmp_path, SEARCH, METADATA, QUERY)
    full = texts(messages)

    search, metadata, query = texts(visible(messages, sources))

    assert json.loads(metadata) == {
        "evidence_id": "E2",
        "note": SPENT_NOTES["metadata"].format(table_id="06913"),
    }
    assert json.loads(search) == {"evidence_id": "E1", "note": SPENT_NOTES["search"]}
    assert query == full[2]
    # The metadata of 06913 lists 1,288 regions; the note replaces it in every later call.
    assert len(full[1]) > 40_000
    assert len(metadata) < 200


def test_metadata_read_again_after_a_query_is_shown_whole(tmp_path: Path) -> None:
    # Otherwise a model that reads the metadata again would only get the note again.
    messages, sources, _ = conversation(tmp_path, METADATA, QUERY, METADATA)
    full = texts(messages)

    first, _, again = texts(visible(messages, sources))

    assert json.loads(first)["evidence_id"] == "E1"
    assert again == full[2]
    assert len(again) > 40_000


def test_a_query_of_another_table_leaves_the_metadata_whole(tmp_path: Path) -> None:
    messages, sources, _ = conversation(tmp_path, OTHER_METADATA, QUERY)

    assert spent(sources) == {}
    assert visible(messages, sources) == messages


def test_a_spent_result_can_still_be_cited(tmp_path: Path) -> None:
    _, _, evidence = conversation(tmp_path, SEARCH, METADATA, QUERY)

    report = check("Tallene kommer fra tabell 06913 [E2].", "no", evidence)

    assert report.passed, report.text()

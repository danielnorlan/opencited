"""The conversation as the model gets it, with spent search and metadata results shortened.

Every model call sends the whole conversation, so a large tool result rides along in
every later call: the metadata of table 06913 is about 17,000 tokens. A search result
is spent once a later query has given a result, and the metadata of a table once a
later query of that table has given one. In later calls the model gets a note
instead, with the result's evidence ID, which it can still cite, and the tool that
reads the result again. The run keeps every full result, and the gate reads those.

A search is not spent when the model has only read metadata: if that table is the
wrong one, the model needs the list again, and a search that it runs once more costs
a model call, which carries far more tokens than the list. Metadata of a table that
no query used stays too, since a question over two tables often reads both before
it queries either.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

from opencited.agent.model import Message, ToolMessage
from opencited.agent.prompt import SPENT_NOTES
from opencited.gate import SourceEvidence
from opencited.sources import QueryResult, SearchResult, TableMetadata


@dataclass(frozen=True)
class SourceMessage:
    """The position of a tool message in the conversation, and the source result it holds."""

    index: int
    evidence: SourceEvidence


def note(evidence_id: str, text: str) -> str:
    return json.dumps({"evidence_id": evidence_id, "note": text}, separators=(",", ":"))


def spent(results: Sequence[SourceMessage]) -> dict[int, str]:
    """Return the note for each spent result, by the position of its tool message."""
    notes: dict[int, str] = {}
    for position, item in enumerate(results):
        later = [other.evidence.result for other in results[position + 1 :]]
        result = item.evidence.result
        name = item.evidence.evidence_id
        if isinstance(result, SearchResult):
            if any(isinstance(other, QueryResult) for other in later):
                notes[item.index] = note(name, SPENT_NOTES["search"])
        elif isinstance(result, TableMetadata) and any(
            isinstance(other, QueryResult) and other.table_id == result.table_id for other in later
        ):
            text = SPENT_NOTES["metadata"].format(table_id=result.table_id)
            notes[item.index] = note(name, text)
    return notes


def visible(messages: Sequence[Message], results: Sequence[SourceMessage]) -> list[Message]:
    """Return the conversation as the model gets it in its next call."""
    notes = spent(results)
    return [
        ToolMessage(message.call_id, notes[index])
        if isinstance(message, ToolMessage) and index in notes
        else message
        for index, message in enumerate(messages)
    ]


__all__ = ["SourceMessage", "spent", "visible"]

"""The system prompt, its version, the retry message and the model's refusal line."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from answering import evidences

from opencited.agent import prompt
from opencited.agent.prompt import (
    PROMPT_VERSION,
    ModelRefusal,
    refusal_in,
    retry_message,
    system_prompt,
)
from opencited.gate import check
from opencited.snapshots import SnapshotStore

# The SHA-256 of the prompt texts for each version. A change to the texts without a
# new version fails here, since every answer records the version it was made with.
TEXTS = {"1": "498d6f9adac3c8d21a517f0b8b4d131d921d75a7b53072d674fc4a70a2d13b99"}


def test_a_change_to_the_prompt_texts_needs_a_new_version() -> None:
    texts = prompt.SYSTEM_PROMPT + prompt.RETRY

    assert hashlib.sha256(texts.encode()).hexdigest() == TEXTS[PROMPT_VERSION]


def test_the_prompt_names_the_language_of_the_answer_and_nothing_else_differs() -> None:
    norwegian, english = system_prompt("no"), system_prompt("en")

    assert "Write the answer in Norwegian (bokmål)." in norwegian
    assert "Write the answer in English." in english
    assert norwegian.replace("Norwegian (bokmål)", "English") == english


def test_the_prompt_teaches_the_citation_markers_and_the_refusal_lines() -> None:
    text = system_prompt("en")

    for part in ("[E1]", '"REFUSE: no data"', '"REFUSE: outside the sources"', "calculate"):
        assert part in text


def test_a_retry_sends_the_gate_report_back(tmp_path: Path) -> None:
    found = evidences(SnapshotStore(tmp_path))
    report = check("I 2026 bodde det 5,7 millioner personer i Norge [E1].", "no", found.evidence)

    message = retry_message(report)

    assert message.startswith("The evidence gate blocked your answer.\n")
    assert report.text() in message
    assert message.endswith("or leave the number out.")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("REFUSE: no data", ModelRefusal("no data", "")),
        (
            "REFUSE: no data\nStatistikkbanken har ingen tabell over dette.\n",
            ModelRefusal("no data", "Statistikkbanken har ingen tabell over dette."),
        ),
        (
            "\n  REFUSE: outside the sources  \nNot statistics.",
            ModelRefusal("outside the sources", "Not statistics."),
        ),
    ],
)
def test_a_reply_that_starts_with_a_refusal_line_is_a_refusal(
    text: str, expected: ModelRefusal
) -> None:
    assert refusal_in(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "refuse: no data",
        "REFUSE: no data at all",
        "REFUSE: outside",
        "Svar [E1].\nREFUSE: no data",
        "I REFUSE: no data",
    ],
)
def test_any_other_reply_is_an_answer(text: str) -> None:
    assert refusal_in(text) is None

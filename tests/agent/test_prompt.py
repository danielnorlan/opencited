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
TEXTS = {
    "1": "0cbf90b1b19623329bbf09b56bd3223ad1cdcb9b31a98afd6b8c932b8411fea2",
    "2": "fa4ea61be2406c0ade156a6d602532dbd5cb00c668d5e9420b73143461639ae3",
}


def test_a_change_to_the_prompt_texts_needs_a_new_version() -> None:
    texts = "".join(
        [
            prompt.SYSTEM_PROMPT,
            prompt.RETRY,
            prompt.UNCITED,
            repr(prompt.LANGUAGES),
            prompt.REFUSAL.pattern,
            repr(prompt.HINTS),
        ]
    )

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
    assert "or leave the number out.\n" in message
    # The failed value gets what to do about it, once.
    assert message.endswith(
        "Write '5,7 millioner' as a result gives it, with its sign and its per cent word, "
        "or leave it out."
    )


def test_a_retry_after_a_failed_date_says_to_write_the_period(tmp_path: Path) -> None:
    found = evidences(SnapshotStore(tmp_path))
    text = "Per 1. januar 2026 og 1. januar 2026 bodde det mange i Norge [E1]."
    report = check(text, "no", found.evidence)

    message = retry_message(report)

    hint = "Write '1. januar 2026' as the period it belongs to, such as the year, or leave the date out."
    assert message.count(hint) == 1


def test_a_retry_for_an_uncited_answer_has_no_hints(tmp_path: Path) -> None:
    found = evidences(SnapshotStore(tmp_path))
    report = check("Ingen tall her.", "no", found.evidence)

    assert retry_message(report) == prompt.RETRY.format(report=report.text())


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
        ("Jeg fant ingenting.\nREFUSE: no data", ModelRefusal("no data", "Jeg fant ingenting.")),
        ("REFUSE: no data.", ModelRefusal("no data", "")),
        ("**REFUSE: no data**", ModelRefusal("no data", "")),
        ("> `refuse: Outside the sources`", ModelRefusal("outside the sources", "")),
    ],
)
def test_a_reply_with_a_refusal_line_is_a_refusal(text: str, expected: ModelRefusal) -> None:
    assert refusal_in(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "REFUSE: no data at all",
        "REFUSE: outside",
        "I REFUSE: no data",
    ],
)
def test_any_other_reply_is_an_answer(text: str) -> None:
    assert refusal_in(text) is None


def test_an_answer_that_cites_nothing_is_asked_to_cite_or_refuse() -> None:
    message = prompt.uncited_message()

    assert message.startswith("Your answer cites no result.")
    assert "[E1]" in message
    assert "refuse" in message

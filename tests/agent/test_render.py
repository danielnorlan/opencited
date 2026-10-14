"""An answer or a refusal as `opencited ask` prints it."""

from __future__ import annotations

from pathlib import Path

import pytest
from answering import answer

from opencited.agent.answer import Refusal
from opencited.agent.render import text_of
from opencited.snapshots import SnapshotStore
from opencited.sources import Language
from opencited.values import format_number


def test_markers_become_numbered_sources_in_norwegian(tmp_path: Path) -> None:
    result, found, _ = answer(SnapshotStore(tmp_path), "no")
    record = found.query.provenance
    moment = record.retrieved_at.strftime("%Y-%m-%d %H:%M")
    growth = format_number(found.step("growth"), "no")
    after = format_number(found.value("2026"), "no")

    assert text_of(result) == "\n".join(
        [
            f"Folketallet økte med {growth} prosent fra 2025 til 2026 [1] [2].",
            "",
            f"I 2026 bodde det {after} personer i Norge [2], ifølge tabell 05810 [3].",
            "",
            "Kilder:",
            "[1] Beregning fra tabell 05810, steg growth. Stegene med SQL står i --json.",
            f"[2] Statistics Norway (SSB), tabell 05810: {record.table_title}. "
            f"{record.table_url}, hentet {moment} UTC",
            "[3] Tabell 05810: Population, by sex and age 1845-2026. "
            "https://www.ssb.no/en/statbank/table/05810",
        ]
    )


def test_markers_become_numbered_sources_in_english(tmp_path: Path) -> None:
    result, found, _ = answer(SnapshotStore(tmp_path), "en")
    record = found.query.provenance
    moment = record.retrieved_at.strftime("%Y-%m-%d %H:%M")

    lines = text_of(result).splitlines()

    assert lines[0].endswith("from 2025 to 2026 [1] [2].")
    assert lines[4:] == [
        "Sources:",
        "[1] Calculation from table 05810, step growth. The steps with their SQL are in --json.",
        f"[2] Statistics Norway (SSB), table 05810: {record.table_title}. "
        f"{record.table_url}, retrieved {moment} UTC",
        "[3] Table 05810: Population, by sex and age 1845-2026. "
        "https://www.ssb.no/en/statbank/table/05810",
    ]


def refusal(
    tmp_path: Path, language: Language, reason: str, limit: str | None, explanation: str | None
) -> Refusal:
    result, _, _ = answer(SnapshotStore(tmp_path), language)
    return Refusal.model_validate(
        {
            "question": result.question,
            "language": language,
            "reason": reason,
            "detail": "a detail for the record",
            "limit": limit,
            "explanation": explanation,
            "report": None,
            "model": result.model,
            "prompt_version": "1",
            "usage": result.usage,
        }
    )


@pytest.mark.parametrize(
    ("language", "reason", "limit", "expected"),
    [
        ("no", "no data", None, "Ikke besvart: Kildene har ingen data for spørsmålet."),
        ("en", "no data", None, "Not answered: The sources hold no data for the question."),
        (
            "no",
            "outside the sources",
            None,
            "Ikke besvart: Spørsmålet gjelder ikke statistikk som SSB publiserer.",
        ),
        (
            "en",
            "outside the sources",
            None,
            "Not answered: The question is not about statistics that SSB publishes.",
        ),
        (
            "no",
            "gate blocked",
            None,
            "Ikke besvart: Hvert svar hadde tall som ikke kunne spores til en kilde.",
        ),
        (
            "en",
            "gate blocked",
            None,
            "Not answered: Every answer held numbers that could not be traced to a source.",
        ),
        ("no", "limit", "time", "Ikke besvart: Kjøringen stoppet ved tidsgrensen."),
        ("no", "limit", "tokens", "Ikke besvart: Kjøringen stoppet ved grensen for tokens."),
        ("no", "limit", "cost", "Ikke besvart: Kjøringen stoppet ved kostnadsgrensen."),
        (
            "no",
            "limit",
            "model calls",
            "Ikke besvart: Kjøringen stoppet ved grensen for modellkall.",
        ),
        (
            "no",
            "limit",
            "tool calls",
            "Ikke besvart: Kjøringen stoppet ved grensen for verktøykall.",
        ),
        ("en", "limit", "time", "Not answered: The run stopped at its time limit."),
        ("en", "limit", "tool calls", "Not answered: The run stopped at its limit of tool calls."),
    ],
)
def test_a_refusal_gives_its_reason_in_the_language_of_the_answer(
    tmp_path: Path, language: Language, reason: str, limit: str | None, expected: str
) -> None:
    assert text_of(refusal(tmp_path, language, reason, limit, None)) == expected


def test_the_model_s_explanation_follows_its_refusal(tmp_path: Path) -> None:
    item = refusal(tmp_path, "no", "no data", None, "Jeg fant ingen tabell over dette.")

    assert text_of(item) == (
        "Ikke besvart: Kildene har ingen data for spørsmålet.\n"
        "Begrunnelse fra modellen: Jeg fant ingen tabell over dette."
    )

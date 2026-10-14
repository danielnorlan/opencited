"""Answers and refusals, assembled from recorded evidence and a real gate report."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
from answering import CONFIG, answer, evidences

from opencited.agent.answer import (
    Answer,
    CalculationCitation,
    CellUse,
    Citation,
    ModelRecord,
    Refusal,
    RunUsage,
    answer_of,
    cited,
    model_record,
    usage_of,
)
from opencited.agent.run import RunState
from opencited.gate import check
from opencited.snapshots import SnapshotStore
from opencited.sources import Language, QueryResult, TableMetadata


def label(query: QueryResult, dimension: str, code: str) -> str:
    values = next(item for item in query.dimensions if item.code == dimension).values
    return next(value.label for value in values if value.code == code)


@pytest.mark.parametrize("language", ["no", "en"])
def test_an_answer_cites_every_result_its_markers_name(tmp_path: Path, language: Language) -> None:
    result, found, report = answer(SnapshotStore(tmp_path), language)
    query = found.query

    assert [item.evidence_id for item in result.citations] == ["E1", "E2"]
    assert result.citations[0] == Citation(
        evidence_id="E1",
        kind="query",
        table_id="05810",
        title=query.title,
        url=query.url,
        provenance=query.provenance,
        cells=(
            CellUse(
                codes=("0", "999B", "Personer", "2026"),
                labels=(
                    label(query, "Kjonn", "0"),
                    label(query, "Alder", "999B"),
                    label(query, "ContentsCode", "Personer"),
                    "2026",
                ),
                value=found.value("2026"),
            ),
        ),
    )
    assert result.citations[0].cells[0].labels[0] == "Both sexes"
    metadata = found.evidence[1].result
    assert isinstance(metadata, TableMetadata)
    assert result.citations[1] == Citation(
        evidence_id="E2",
        kind="metadata",
        table_id="05810",
        title=metadata.title,
        url=metadata.url,
        provenance=None,
        cells=(),
    )
    assert result.calculations == (
        CalculationCitation(evidence_id="C1", result=found.calculation, steps=("growth",)),
    )
    assert (result.report, result.language, result.prompt_version) == (report, language, "1")


def test_an_answer_records_the_model_its_settings_and_the_usage(tmp_path: Path) -> None:
    result, _, _ = answer(SnapshotStore(tmp_path))

    assert result.model == ModelRecord(
        base_url="https://models.example.org/v1",
        model="a-model",
        reported=("a-model-2026-10-01",),
        settings=CONFIG.settings,
        prices=CONFIG.prices,
    )
    assert result.usage == RunUsage(
        model_calls=3,
        tool_calls=4,
        input_tokens=12_000,
        output_tokens=900,
        seconds=Decimal("41.250"),
        cost=Decimal("0.003738"),
    )


def test_a_search_result_is_cited_without_a_table(tmp_path: Path) -> None:
    found = evidences(SnapshotStore(tmp_path))
    text = "Søket fant tabeller om befolkningen [E3]."
    report = check(text, "no", found.evidence)

    result = answer_of(
        question="Hvilke tabeller handler om befolkningen?",
        language="no",
        text=text,
        report=report,
        evidence=found.evidence,
        model=model_record(CONFIG, []),
        prompt_version="1",
        usage=usage_of(RunState(), None),
    )

    assert result.citations == (
        Citation(
            evidence_id="E3",
            kind="search",
            table_id=None,
            title=None,
            url=None,
            provenance=None,
            cells=(),
        ),
    )
    assert result.usage.cost is None


def test_markers_are_cited_once_in_the_order_they_first_appear() -> None:
    assert cited("A [E2] and [C1], then [E2] and [E10]; not E3 or [X1] or [E0].") == [
        "E2",
        "C1",
        "E10",
    ]


def test_an_answer_and_a_refusal_hold_no_float_and_read_back_the_same(tmp_path: Path) -> None:
    result, _, report = answer(SnapshotStore(tmp_path))
    refusal = Refusal(
        question=result.question,
        language="no",
        reason="gate blocked",
        detail="the gate blocked 3 answers in a row",
        limit=None,
        explanation=None,
        report=report,
        model=result.model,
        prompt_version="1",
        usage=result.usage,
    )

    for item in (result, refusal):
        text = item.model_dump_json()

        def no_float(number: str) -> object:
            raise AssertionError(f"a float in the JSON: {number}")

        json.loads(text, parse_float=no_float)
        assert type(item).model_validate_json(text) == item
    assert Answer.model_validate_json(result.model_dump_json()).kind == "answer"
    assert json.loads(refusal.model_dump_json())["kind"] == "refusal"

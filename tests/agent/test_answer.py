"""Answers and refusals, assembled from recorded evidence and a real gate report."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest
from answering import CONFIG, TOTAL, answer, evidences
from snapshotting import catch, population, retrieve

from opencited.agent.answer import (
    Answer,
    CalculationCitation,
    CellUse,
    Citation,
    ModelRecord,
    Refusal,
    RunUsage,
    answer_of,
    model_record,
    usage_of,
)
from opencited.agent.run import RunState
from opencited.calc.engine import Engine
from opencited.calc.plan import Plan
from opencited.gate import CalculationEvidence, Evidence, SourceEvidence, check
from opencited.snapshots import SnapshotStore
from opencited.sources import Language, QueryResult, TableMetadata
from opencited.values import format_number


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


def test_each_citation_lists_only_its_own_cells_and_steps(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path)
    found = evidences(store)
    retrieval = catch()
    second = Engine(store).run(
        Plan.model_validate_json(
            json.dumps(
                {
                    "steps": [
                        {
                            "name": "earlier",
                            "operation": "value",
                            "snapshot": store.write(population()).name,
                            "codes": TOTAL | {"Tid": "2025"},
                        }
                    ]
                }
            )
        )
    )
    evidence = (
        *found.evidence,
        SourceEvidence("E4", retrieval.result),
        CalculationEvidence("C2", second),
    )
    caught = next(
        cell
        for cell in retrieval.result.observations
        if cell.codes == ("80a", "18", "Fangst", "2016")
    )
    assert caught.value is not None
    text = (
        f"I 2026 bodde det {format_number(found.value('2026'), 'no')} personer i Norge [E1].\n\n"
        f"Året før var det {format_number(found.value('2025'), 'no')} [C2], og veksten var "
        f"{format_number(found.step('growth'), 'no')} prosent [C1].\n\n"
        f"Fangsten var {format_number(caught.value, 'no')} kg i 2016 [E4]."
    )
    report = check(text, "no", evidence)
    assert report.passed, report.text()

    result = answer_of(
        question="Hvor mange bor i Norge?",
        language="no",
        text=text,
        report=report,
        evidence=evidence,
        model=model_record(CONFIG, []),
        prompt_version="1",
        usage=usage_of(RunState(), None),
    )

    assert [
        (item.evidence_id, [cell.codes for cell in item.cells]) for item in result.citations
    ] == [
        ("E1", [("0", "999B", "Personer", "2026")]),
        ("E4", [("80a", "18", "Fangst", "2016")]),
    ]
    # The value of 2025 fits a step of each calculation its paragraph cites.
    assert [(item.evidence_id, item.steps) for item in result.calculations] == [
        ("C2", ("earlier",)),
        ("C1", ("before", "growth")),
    ]


def test_two_queries_of_one_table_each_list_the_cells_cited_with_them(tmp_path: Path) -> None:
    found = evidences(SnapshotStore(tmp_path))
    selection = {"Kjonn": ["0"], "Alder": ["999B"], "ContentsCode": ["Personer"]}
    norwegian = retrieve("05810", selection | {"Tid": ["2025", "2026"]}, "no").result
    evidence = (*found.evidence, SourceEvidence("E4", norwegian))
    text = (
        f"I 2026 bodde det {format_number(found.value('2026'), 'no')} personer i Norge [E1].\n\n"
        f"I 2025 bodde det {format_number(found.value('2025'), 'no')} personer i Norge [E4]."
    )
    report = check(text, "no", evidence)
    assert report.passed, report.text()

    result = answer_of(
        question="Hvor mange bor i Norge?",
        language="no",
        text=text,
        report=report,
        evidence=evidence,
        model=model_record(CONFIG, []),
        prompt_version="1",
        usage=usage_of(RunState(), None),
    )

    assert [
        (item.evidence_id, [cell.codes for cell in item.cells]) for item in result.citations
    ] == [
        ("E1", [("0", "999B", "Personer", "2026")]),
        ("E4", [("0", "999B", "Personer", "2025")]),
    ]


def released(found_evidence: tuple[object, ...], text: str) -> Answer:
    evidence = cast("tuple[Evidence, ...]", found_evidence)
    report = check(text, "no", evidence)
    assert report.passed, report.text()
    return answer_of(
        question="Hvor mange bor i Norge?",
        language="no",
        text=text,
        report=report,
        evidence=evidence,
        model=model_record(CONFIG, []),
        prompt_version="1",
        usage=usage_of(RunState(), None),
    )


def test_a_number_that_fits_several_cells_lists_every_one(tmp_path: Path) -> None:
    found = evidences(SnapshotStore(tmp_path))

    result = released(found.evidence, "Det bor om lag 5,6 millioner personer i Norge [E1].")

    assert [cell.codes for cell in result.citations[0].cells] == [
        ("0", "999B", "Personer", year) for year in ("2024", "2025", "2026")
    ]


def test_a_marker_in_an_html_comment_cites_nothing(tmp_path: Path) -> None:
    found = evidences(SnapshotStore(tmp_path))
    value = format_number(found.value("2026"), "no")

    result = released(
        found.evidence, f"I 2026 bodde det {value} personer i Norge [E1]. <!-- [E3] [C1] -->"
    )

    assert [item.evidence_id for item in result.citations] == ["E1"]
    assert result.calculations == ()

"""The gate's rules one by one, with evidence from the recordings.

The answers are written for the tests; their numbers are formatted from recorded
values or taken from the recorded results' own fields.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from recorded import FIXTURES
from snapshotting import population

from opencited.calc import CalculationResult
from opencited.gate.blocks import blocks
from opencited.gate.check import CalculationEvidence, SourceEvidence, check
from opencited.http import make_client
from opencited.http.replay import replay_transport
from opencited.sources import QueryResult, SearchResult, TableMetadata
from opencited.sources.ssb import BASE_URL, SsbClient
from opencited.values import format_number


def first_value(result: QueryResult) -> Decimal:
    value = result.observations[0].value
    assert value is not None
    return value


def metadata() -> TableMetadata:
    async def read() -> TableMetadata:
        async with make_client(BASE_URL, transport=replay_transport(FIXTURES)) as http:
            return await SsbClient(http, revision_period=False).metadata("05810", "en")

    return asyncio.run(read())


def search() -> SearchResult:
    async def read() -> SearchResult:
        async with make_client(BASE_URL, transport=replay_transport(FIXTURES)) as http:
            return await SsbClient(http, revision_period=False).search("population", "en")

    return asyncio.run(read())


@pytest.mark.parametrize("name", ["E0", "C1", "E", "e1", "E01"])
def test_a_source_needs_an_evidence_id_of_e_and_a_number(name: str) -> None:
    with pytest.raises(ValueError, match="evidence ID"):
        SourceEvidence(name, population().result)


@pytest.mark.parametrize("name", ["C0", "E1", "C", "c2"])
def test_a_calculation_needs_an_evidence_id_of_c_and_a_number(name: str) -> None:
    with pytest.raises(ValueError, match="evidence ID"):
        CalculationEvidence(name, CalculationResult(steps=(), snapshots=()))


def test_two_results_may_not_share_an_evidence_id() -> None:
    result = population().result
    with pytest.raises(ValueError, match="same evidence ID"):
        check("Tekst.", "no", [SourceEvidence("E1", result), SourceEvidence("E1", result)])


def test_metadata_as_evidence_holds_its_table_id_and_update_date() -> None:
    table = metadata()
    answer = f"Table {table.table_id} was updated on {table.updated.date().isoformat()} [E1]."
    report = check(answer, "en", [SourceEvidence("E1", table)])
    assert report.passed, report.text()
    assert [finding.kind for finding in report.findings] == ["table ID", "date"]


def test_a_search_result_holds_the_ids_of_the_tables_it_found() -> None:
    found = search()
    first, second = found.tables[0], found.tables[1]
    answer = f"Tables {first.table_id} and {second.table_id} [E1] match, but not 99999 [E1]."
    report = check(answer, "en", [SourceEvidence("E1", found)])
    assert [finding.outcome for finding in report.findings] == ["passed", "passed", "failed"]


def test_a_retrieval_date_passes_in_utc_and_in_norwegian_time() -> None:
    result = population().result
    moment = result.provenance.retrieved_at
    assert moment.tzinfo is not None
    for day in {moment.date(), moment.astimezone(ZoneInfo("Europe/Oslo")).date()}:
        report = check(f"Retrieved {day.isoformat()} [E1].", "en", [SourceEvidence("E1", result)])
        assert report.passed, report.text()


def test_markers_are_no_findings_and_list_numbers_are_ignored() -> None:
    result = population().result
    value = format_number(first_value(result), "en")
    report = check(f"1. The first was {value} [E1].", "en", [SourceEvidence("E1", result)])
    assert [(finding.kind, finding.outcome) for finding in report.findings] == [
        ("list number", "ignored"),
        ("value", "matched"),
    ]
    assert report.findings[1].evidence == "E1"
    assert report.findings[1].detail.startswith("cell ")


def test_the_report_names_each_failed_number_with_its_reason() -> None:
    report = check("It was 42 and 2019-05-05.", "en", [])
    assert not report.passed
    text = report.text()
    assert text.startswith("The gate blocked the answer: 2 of 2 numbers failed.")
    assert "'42' at 7: a value in a block that cites no result" in text
    assert "'2019-05-05'" in text


def test_a_report_without_failures_says_how_many_numbers_passed() -> None:
    result = population().result
    report = check(
        f"The data are from {result.table_id} [E1].", "en", [SourceEvidence("E1", result)]
    )
    assert report.text() == "The gate passed every number (1 checked)."


def test_findings_come_in_the_order_of_the_text() -> None:
    result = population().result
    value = format_number(first_value(result), "en")
    answer = f"First 7.\n\nThen {value} [E1].\n\n- And 2026 [E1]."
    report = check(answer, "en", [SourceEvidence("E1", result)])
    positions = [finding.position for finding in report.findings]
    assert positions == sorted(positions)


def test_a_failed_citation_is_a_finding() -> None:
    report = check("See [C3].", "no", [])
    [finding] = report.findings
    assert (finding.kind, finding.outcome) == ("citation", "failed")


def test_blocks_split_paragraphs_items_rows_and_headings() -> None:
    text = "# Title\n\nOne line\nand two.\n\n- item\n  more\n- next\nafter\n\n| a | b |\n| --- | --- |\n| 1 | 2 |"
    assert [text[block.start : block.end].strip() for block in blocks(text)] == [
        "# Title",
        "One line\nand two.",
        "- item\n  more",
        "- next",
        "after",
        "| a | b |",
        "| 1 | 2 |",
    ]

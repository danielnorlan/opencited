"""The gate's rules one by one, with evidence from the recordings.

The answers are written for the tests. A number that an answer must match comes from
the recorded results, formatted by the project's formatter or taken from their own
fields; a number that must fail is written by hand. Tests that need a unit, a time or
a period that no recording has use `altered` results, which stand for no response.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from recorded import FIXTURES
from snapshotting import altered, population, retrieve, symbols

from opencited.calc import CalculationResult, Engine, Plan, ResultUnit, StepResult
from opencited.gate.blocks import blocks
from opencited.gate.check import (
    CalculationEvidence,
    Match,
    SourceEvidence,
    check,
    cited_ids,
    day_months,
    language_of,
    reference_dates,
)
from opencited.http import make_client
from opencited.http.replay import replay_transport
from opencited.snapshots import SnapshotStore
from opencited.sources import Language, QueryResult, SearchResult, TableMetadata, Unit
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
    assert report.findings[1].matches == (Match(evidence="E1", cell=result.observations[0].codes),)


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


def test_a_count_with_a_percentage_point_word_fails() -> None:
    result = population().result
    value = format_number(first_value(result), "en")
    report = check(f"It was {value} percentage points [E1].", "en", [SourceEvidence("E1", result)])
    assert not report.passed


def tied(exact: str) -> CalculationResult:
    """A calculation whose one step has the exact result ``exact`` and no rounding."""
    step = StepResult(
        name="mean",
        operation="mean",
        inputs=(),
        snapshot=None,
        sql=(),
        parameters=(),
        arithmetic="",
        exact=exact,
        value=Decimal(0),
        decimals=0,
        unit=ResultUnit(kind="amount", name="kg"),
    )
    return CalculationResult(steps=(step,), snapshots=())


@pytest.mark.parametrize(
    ("exact", "written", "passes"),
    [
        ("49/20", "2.5", True),
        ("49/20", "2.4", False),
        ("-49/20", "-2.5", True),
        ("-49/20", "-2.4", False),
    ],
)
def test_a_tie_of_an_exact_result_rounds_away_from_zero(
    exact: str, written: str, passes: bool
) -> None:
    # 49/20 is 2.45: half-even rounding would give 2.4, the gate's rule gives 2.5.
    report = check(f"It was {written} kg [C1].", "en", [CalculationEvidence("C1", tied(exact))])
    assert report.passed is passes
    if passes:
        assert report.findings[0].matches == (Match(evidence="C1", step="mean"),)


def test_a_cell_whose_unit_names_per_cent_needs_a_percent_word() -> None:
    # The units of the data response, which the gate reads; no recorded table has these.
    units = (Unit(code="Personer", base="Prosent av befolkningen", decimals=1),)
    result = population().result.model_copy(update={"units": units})
    value = format_number(first_value(result), "no")
    evidence = [SourceEvidence("E1", result)]
    assert not check(f"Det var {value} [E1].", "no", evidence).passed
    assert check(f"Det var {value} prosent [E1].", "no", evidence).passed


def test_the_cells_of_a_ranking_are_values_of_the_calculation(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path)
    snapshot = store.write(population()).name
    step = {
        "name": "top",
        "operation": "rank",
        "snapshot": snapshot,
        "selection": {"Kjonn": ["0"], "ContentsCode": ["Personer"], "Tid": ["2026"]},
        "by": "Alder",
        "order": "highest",
        "count": 2,
    }
    result = Engine(store).run(Plan.model_validate_json(json.dumps({"steps": [step]})))
    second = result.step("top").ranked[1]
    report = check(
        f"Next came {format_number(second.value, 'en')} [C1].",
        "en",
        [CalculationEvidence("C1", result)],
    )
    assert report.passed, report.text()
    assert report.findings[0].detail.startswith("step top, ")
    assert report.findings[0].matches[0] == Match(evidence="C1", cell=second.codes, step="top")


def test_a_retrieval_just_after_midnight_in_oslo_has_both_dates() -> None:
    base = population().result
    late = datetime(2026, 10, 13, 23, 30, tzinfo=UTC)
    record = base.provenance.model_copy(update={"retrieved_at": late})
    result = base.model_copy(update={"provenance": record})
    evidence = [SourceEvidence("E1", result)]
    for day in ("2026-10-13", "2026-10-14"):
        assert check(f"Retrieved {day} [E1].", "en", evidence).passed, day
    assert not check("Retrieved 2026-10-15 [E1].", "en", evidence).passed


def test_a_missing_value_is_no_value() -> None:
    result = symbols().result
    assert [cell for cell in result.observations if cell.value is None]
    assert all(cell.value != 0 for cell in result.observations if cell.value is not None)
    assert not check("It was 0 [E1].", "en", [SourceEvidence("E1", result)]).passed


def test_numbers_in_a_title_pass_as_written() -> None:
    table = metadata()
    [year, *_] = [token for token in table.title.split() if token[:1].isdigit()]
    first = year.split("-")[0]
    evidence = [SourceEvidence("E1", table)]
    assert check(f"From {first} [E1].", "en", evidence).passed
    assert not check(f"From {first} million [E1].", "en", evidence).passed


@pytest.mark.parametrize("written", ["2,026", "2026.0"])
def test_a_year_written_otherwise_is_no_period_or_label(written: str) -> None:
    result = population().result
    assert not check(f"In {written} [E1].", "en", [SourceEvidence("E1", result)]).passed


def test_a_label_number_with_a_scale_word_fails() -> None:
    result = population().result
    assert check("Aged 16 [E1].", "en", [SourceEvidence("E1", result)]).passed
    assert not check("Aged 16 million [E1].", "en", [SourceEvidence("E1", result)]).passed


def test_metadata_selects_no_period_and_a_calculation_holds_no_id_or_date(tmp_path: Path) -> None:
    table = metadata()
    assert not check("In 2025 [E1].", "en", [SourceEvidence("E1", table)]).passed
    store = SnapshotStore(tmp_path)
    snapshot = store.write(population()).name
    step = {
        "name": "now",
        "operation": "value",
        "snapshot": snapshot,
        "codes": {"Kjonn": "0", "Alder": "999B", "ContentsCode": "Personer", "Tid": "2026"},
    }
    result = Engine(store).run(Plan.model_validate_json(json.dumps({"steps": [step]})))
    evidence = [CalculationEvidence("C1", result)]
    assert not check("Table 05810 [C1].", "en", evidence).passed
    assert not check(f"Updated {table.updated.date().isoformat()} [C1].", "en", evidence).passed


def test_a_period_in_words_passes_only_when_selected() -> None:
    # Quarters, which no recorded table has.
    base = population()
    dimensions = list(base.result.dimensions)
    time = dimensions[3]
    codes = ["2025K4", "2026K1", "2026K2"]
    dimensions[3] = time.model_copy(
        update={
            "values": tuple(
                value.model_copy(update={"code": code, "label": code})
                for value, code in zip(time.values, codes, strict=True)
            )
        }
    )
    rename = dict(zip([value.code for value in time.values], codes, strict=True))
    cells = [
        cell.model_copy(update={"codes": (*cell.codes[:3], rename[cell.codes[3]])})
        for cell in base.result.observations
    ]
    result = altered(base, dimensions=dimensions, cells=cells).result
    evidence = [SourceEvidence("E1", result)]
    assert check("I 1. kvartal 2026 [E1].", "no", evidence).passed
    assert not check("I 4. kvartal 2026 [E1].", "no", evidence).passed


def test_a_marker_in_an_html_comment_cites_nothing() -> None:
    result = population().result
    value = format_number(first_value(result), "en")
    assert not check(f"It was {value} <!-- [E1] -->", "en", [SourceEvidence("E1", result)]).passed


@pytest.mark.parametrize(
    "template",
    [
        "> - A {v} [E1]\n> - B {v}",
        "> A {v} [E1]\n>\n> B {v}",
        "a | b\n--- | ---\nx | {v} [E1]\ny | {v}",
        "A {v} [E1]\n---\nB {v}",
        "A {v} [E1]\n\n```\ncode\n```\nB {v}",
        "A {v} [E1]\n```\ncode\n```\nB {v}",
        "> A {v} [E1]\nB {v}",
    ],
)
def test_a_citation_reaches_no_number_outside_its_block(template: str) -> None:
    result = population().result
    value = format_number(first_value(result), "en")
    report = check(template.format(v=value), "en", [SourceEvidence("E1", result)])
    assert [finding.outcome for finding in report.findings if finding.kind == "value"] == [
        "matched",
        "failed",
    ]


def test_language_of_reads_the_link() -> None:
    assert language_of("https://www.ssb.no/en/statbank/table/05810") == "en"
    assert language_of("https://www.ssb.no/statbank/table/05810") == "no"


def test_a_long_answer_is_checked_quickly() -> None:
    result = population().result
    value = format_number(first_value(result), "en")
    rows = "\n".join(f"| 2026 | {value} [E1] |" for _ in range(200))
    started = time.perf_counter()
    report = check(
        f"| Year | People |\n| --- | --- |\n{rows}", "en", [SourceEvidence("E1", result)]
    )
    assert report.passed
    assert time.perf_counter() - started < 10


def test_an_overlong_number_fails_with_a_report() -> None:
    report = check(
        "It was 0." + "1" * 5000 + " [E1].", "en", [SourceEvidence("E1", population().result)]
    )
    assert not report.passed


def test_the_same_answer_gets_the_same_report_in_other_processes() -> None:
    script = (
        "import sys; sys.path[:0] = ['tests']; import snapshotting as s; "
        "from opencited.gate import Match, cited_ids, check, SourceEvidence; "
        "print(check(sys.argv[1], 'en', [SourceEvidence('E1', s.population().result)]).model_dump_json())"
    )
    answer = "In 2026 it was 5,627,400 [E1], and 7 in 2019 [E1]."
    outputs = {
        subprocess.run(
            [sys.executable, "-c", script, answer],
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, "PYTHONHASHSEED": seed},
            cwd=Path(__file__).resolve().parents[2],
        ).stdout
        for seed in ("0", "1")
    }
    assert len(outputs) == 1


def test_an_update_just_before_midnight_in_utc_has_both_dates() -> None:
    base = population().result
    late = datetime(2026, 2, 24, 23, 30, tzinfo=UTC)
    result = base.model_copy(update={"updated": late})
    evidence = [SourceEvidence("E1", result)]
    for day in ("2026-02-24", "2026-02-25"):
        assert check(f"Updated {day} [E1].", "en", evidence).passed, day


def test_a_number_that_fits_several_cited_values_lists_them_all() -> None:
    result = population().result
    report = check(
        "Det bodde om lag 5,6 millioner i Norge [E1].", "no", [SourceEvidence("E1", result)]
    )
    totals = [
        cell.codes for cell in result.observations if cell.codes[:3] == ("0", "999B", "Personer")
    ]

    assert report.passed, report.text()
    [found] = report.findings
    assert [match.cell for match in found.matches] == totals
    assert {match.evidence for match in found.matches} == {"E1"}
    assert found.detail == f"cell {', '.join(totals[0])}"


def test_shown_markers_are_cited_in_order_and_hidden_ones_are_not() -> None:
    text = "Først [E2], så [C1] og [E2] igjen. <!-- [E7] --> Til slutt [E10]."

    assert cited_ids(text, "no") == ["E2", "C1", "E10"]


# The day of a query result's figures (spec 0003, numbers that are not values, item 2).

NORWAY = {"Region": ["0"], "ContentsCode": ["Folkemengde"], "Tid": ["2025", "2026"]}
OSLO_EN = {"Region": ["0301"], "ContentsCode": ["Folkemengde"], "Tid": ["2025", "2026"]}


def population_of(selection: dict[str, list[str]], language: Language) -> QueryResult:
    return retrieve("06913", selection, language).result


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ("Befolkning 1. januar", {(1, 1)}),
        ("Population 1 January", {(1, 1)}),
        ("Folkemengde per 31.12.", {(12, 31)}),
        # A decimal, a longer dotted number or a month past 12 is no day.
        ("Andel 2.5 prosent", set[tuple[int, int]]()),
        ("Endring 1.5 januar", set[tuple[int, int]]()),
        ("Kode 3.1.12.", set[tuple[int, int]]()),
        ("Kode 31.13.", set[tuple[int, int]]()),
    ],
)
def test_the_day_and_month_that_a_label_names(text: str, found: set[tuple[int, int]]) -> None:
    assert day_months(text) == found


def test_the_year_of_the_day_comes_from_the_time_dimension_only() -> None:
    # The region code 0301 has four digits but is not a year.
    result = population_of(OSLO_EN, "en")

    assert reference_dates(result) == {date(2025, 1, 1), date(2026, 1, 1)}


@pytest.mark.parametrize(
    ("text", "passes"),
    [
        ("Per 1. januar 2026 bodde det {value} personer i Norge [E1].", True),
        ("Den 1.1.2026 bodde det {value} personer i Norge [E1].", True),
        ("Per 1. januar 2024 bodde det {value} personer i Norge [E1].", False),
        ("Per 2. januar 2026 bodde det {value} personer i Norge [E1].", False),
    ],
)
def test_a_day_that_a_label_names_passes_in_a_selected_year(text: str, passes: bool) -> None:
    result = population_of(NORWAY, "no")
    value = format_number(Decimal(5627400), "no")
    assert any(cell.value == Decimal(5627400) for cell in result.observations)

    report = check(text.format(value=value), "no", [SourceEvidence("E1", result)])

    assert report.passed is passes, report.text()
    [found] = [item for item in report.findings if item.kind == "date"]
    if passes:
        assert (
            found.detail == "a day that a cited query result's label names, in a year it selected"
        )
        assert found.evidence == "E1"


def test_the_day_reads_in_english_labels_too() -> None:
    result = population_of(OSLO_EN, "en")
    label = next(
        value.label
        for dimension in result.dimensions
        for value in dimension.values
        if value.code == "Folkemengde"
    )
    assert label == "Population 1 January"

    report = check(
        "On 1 January 2025, 724,290 people lived in Oslo [E1].",
        "en",
        [SourceEvidence("E1", result)],
    )

    assert report.passed, report.text()


def test_the_day_and_the_year_come_from_the_same_result() -> None:
    # 05810 selects 2024 but names no day; 06913 names 1 January but selects 2025 and 2026.
    other = population().result
    evidence = [SourceEvidence("E1", population_of(NORWAY, "no")), SourceEvidence("E2", other)]
    assert any(value.code == "2024" for dimension in other.dimensions for value in dimension.values)

    report = check("Per 1. januar 2024 var tallene andre [E1] [E2].", "no", evidence)

    assert not report.passed


def test_metadata_selects_no_year_for_the_day_of_its_figures() -> None:
    report = check("Per 1. januar 2026 [E1].", "no", [SourceEvidence("E1", metadata())])

    assert not report.passed

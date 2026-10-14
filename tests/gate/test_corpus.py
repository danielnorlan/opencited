"""The corpus of example answers: each with the decision the gate must reach and its rule.

The evidence is recorded data from `tests/fixtures/ssb/` and calculations over its
snapshots. Every number that an answer must match is formatted from that evidence by
the project's formatter. The numbers that an answer must not match, such as another
table's ID or a date that no result has, and the words around the numbers, are
written for the tests.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction

import pytest
from snapshotting import catch, population, retrieve

from opencited.calc import CalculationResult, Engine, Plan
from opencited.calc.arithmetic import round_away
from opencited.gate.check import CalculationEvidence, Evidence, Report, SourceEvidence, check
from opencited.gate.numbers import Language
from opencited.snapshots import SnapshotStore
from opencited.sources import QueryResult
from opencited.values import format_number

# The recorded Norwegian query: the whole population in 2025 and 2026.
POPULATION_NO = {
    "Kjonn": ["0"],
    "Alder": ["999B"],
    "ContentsCode": ["Personer"],
    "Tid": ["2025", "2026"],
}


@dataclass(frozen=True)
class Run:
    """The evidence of one run: query results in both languages and calculations."""

    english: QueryResult
    norwegian: QueryResult
    calculation: CalculationResult
    fish: CalculationResult

    def evidence(self, language: Language) -> list[Evidence]:
        query = self.english if language == "en" else self.norwegian
        return [
            SourceEvidence("E1", query),
            CalculationEvidence("C1", self.calculation),
            CalculationEvidence("C2", self.fish),
        ]

    def cell(self, year: str, sex: str = "0", age: str = "999B") -> Decimal:
        [value] = [
            cell.value
            for cell in self.english.observations
            if cell.codes == (sex, age, "Personer", year)
        ]
        assert value is not None
        return value

    def step(self, name: str, result: CalculationResult | None = None) -> Fraction:
        exact = (result or self.calculation).step(name).exact
        assert exact is not None
        return Fraction(exact)


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory) -> Run:
    store = SnapshotStore(tmp_path_factory.mktemp("snapshots"))
    recorded = population()
    english = recorded.result
    norwegian = retrieve("05810", POPULATION_NO, "no").result
    people = store.write(recorded).name
    fish = store.write(catch()).name

    def codes(year: str, sex: str = "0") -> dict[str, str]:
        return {"Kjonn": sex, "Alder": "999B", "ContentsCode": "Personer", "Tid": year}

    steps = [
        {"name": "now", "operation": "value", "snapshot": people, "codes": codes("2026")},
        {"name": "before", "operation": "value", "snapshot": people, "codes": codes("2025")},
        {"name": "women", "operation": "value", "snapshot": people, "codes": codes("2026", "2")},
        {
            "name": "women_before",
            "operation": "value",
            "snapshot": people,
            "codes": codes("2025", "2"),
        },
        {
            "name": "growth",
            "operation": "change_percent",
            "old": "before",
            "new": "now",
            "decimals": 2,
        },
        {
            "name": "share",
            "operation": "share_percent",
            "part": "women",
            "total": "now",
            "decimals": 2,
        },
        {
            "name": "share_before",
            "operation": "share_percent",
            "part": "women_before",
            "total": "before",
            "decimals": 2,
        },
        {"name": "points", "operation": "subtract", "left": "share", "right": "share_before"},
        {"name": "ratio", "operation": "divide", "left": "now", "right": "before", "decimals": 4},
    ]
    calculation = Engine(store).run(Plan.model_validate_json(json.dumps({"steps": steps})))
    selection = {
        "FiskeslagSamiske": ["80a"],
        "SamiskeElver": ["18"],
        "ContentsCode": ["Gjennomsnitt"],
        "Tid": ["2015", "2016"],
    }
    mean = [
        {
            "name": "mean",
            "operation": "mean",
            "snapshot": fish,
            "selection": selection,
            "decimals": 3,
        }
    ]
    fish_result = Engine(store).run(Plan.model_validate_json(json.dumps({"steps": mean})))
    return Run(english, norwegian, calculation, fish_result)


def n(
    value: Decimal | Fraction, language: Language, places: int | None = None, scale: int = 0
) -> str:
    """Format a value of the evidence, rounded as the answer writes it."""
    if places is not None or isinstance(value, Fraction):
        value = round_away(Fraction(value) / 10**scale, places or 0)
    return format_number(Decimal(value), language)


@dataclass(frozen=True)
class Case:
    name: str
    language: Language
    answer: Callable[[Run], str]
    passes: bool
    rule: str


CASES = [
    Case(
        "exact value, Norwegian",
        "no",
        lambda r: f"Folketallet var {n(r.cell('2026'), 'no')} i 2026 [E1].",
        True,
        "Matching values 2",
    ),
    Case(
        "exact value, English",
        "en",
        lambda r: f"The population was {n(r.cell('2026'), 'en')} in 2026 [E1].",
        True,
        "Matching values 2",
    ),
    Case(
        "scale word mill.",
        "no",
        lambda r: f"Det bodde {n(r.cell('2026'), 'no', 1, 6)} mill. i Norge [E1].",
        True,
        "Matching values 2, scale",
    ),
    Case(
        "scale word million",
        "en",
        lambda r: f"About {n(r.cell('2026'), 'en', 2, 6)} million people [E1].",
        True,
        "Matching values 2, scale",
    ),
    Case(
        "wrong scale word",
        "no",
        lambda r: f"Det bodde {n(r.cell('2026'), 'no', 1, 6)} tusen i Norge [E1].",
        False,
        "Matching values 2, scale",
    ),
    Case(
        "value rounded wrongly",
        "en",
        lambda r: f"About {n(r.cell('2026') + 100000, 'en', 1, 6)} million people [E1].",
        False,
        "Matching values 2",
    ),
    Case(
        "more decimals than the value",
        "no",
        lambda r: f"Folketallet var {n(r.cell('2026'), 'no', 1)} [E1].",
        True,
        "Matching values 2, decimals written",
    ),
    Case(
        "change in per cent",
        "no",
        lambda r: (
            f"Folketallet økte med {n(r.step('growth'), 'no', 2)} prosent fra 2025 [C1] [E1]."
        ),
        True,
        "Matching values 3, per cent",
    ),
    Case(
        "change with more decimals of the exact result",
        "en",
        lambda r: f"It grew by {n(r.step('growth'), 'en', 4)} per cent [C1].",
        True,
        "Matching values 2, exact result",
    ),
    Case(
        "per cent without a percent word",
        "en",
        lambda r: f"It grew by {n(r.step('growth'), 'en', 2)} [C1].",
        False,
        "Matching values 3",
    ),
    Case(
        "share in per cent",
        "en",
        lambda r: f"Women were {n(r.step('share'), 'en', 2)}% of the population [C1].",
        True,
        "Matching values 3, per cent",
    ),
    Case(
        "percentage points",
        "no",
        lambda r: f"Andelen endret seg med {n(r.step('points'), 'no', 2)} prosentpoeng [C1].",
        True,
        "Matching values 3, percentage points",
    ),
    Case(
        "percentage points written as per cent",
        "no",
        lambda r: f"Andelen endret seg med {n(r.step('points'), 'no', 2)} prosent [C1].",
        False,
        "Matching values 3, percentage points",
    ),
    Case(
        "a count written as per cent",
        "en",
        lambda r: f"It was {n(r.cell('2026'), 'en')} per cent [E1].",
        False,
        "Matching values 3",
    ),
    Case(
        "ratio",
        "en",
        lambda r: f"The ratio was {n(r.step('ratio'), 'en', 4)} [C1].",
        True,
        "Matching values 2, ratio",
    ),
    Case(
        "wrong sign",
        "en",
        lambda r: f"It changed by -{n(r.step('growth'), 'en', 2)} per cent [C1].",
        False,
        "Matching values 4",
    ),
    Case(
        "value without a citation",
        "no",
        lambda r: f"Folketallet var {n(r.cell('2026'), 'no')} i 2026.",
        False,
        "Matching values 1",
    ),
    Case(
        "citation of a result the run lacks",
        "en",
        lambda r: f"The population was {n(r.cell('2026'), 'en')} [E7].",
        False,
        "Input 2",
    ),
    Case(
        "citation in another paragraph",
        "no",
        lambda r: f"Folketallet var {n(r.cell('2026'), 'no')}.\n\nKilde: [E1].",
        False,
        "Input 3, blocks",
    ),
    Case(
        "a year that only a calculation holds",
        "no",
        lambda r: f"Folketallet økte med {n(r.step('growth'), 'no', 2)} prosent fra 2025 [C1].",
        False,
        "Numbers that are not values 1",
    ),
    Case(
        "selected year",
        "en",
        lambda r: f"In 2025 the population was {n(r.cell('2025'), 'en')} [E1].",
        True,
        "Numbers that are not values 1",
    ),
    Case(
        "year the result did not select",
        "en",
        lambda r: f"In 2019 the population was {n(r.cell('2025'), 'en')} [E1].",
        False,
        "Numbers that are not values 1",
    ),
    Case(
        "update date",
        "en",
        lambda r: f"The table was updated {r.english.updated.date().isoformat()} [E1].",
        True,
        "Numbers that are not values 2",
    ),
    Case(
        "a date the result does not have",
        "en",
        lambda r: "The table was updated 2020-01-01 [E1].",
        False,
        "Numbers that are not values 2",
    ),
    Case(
        "table ID",
        "no",
        lambda r: f"Tabell {r.norwegian.table_id} viser dette [E1].",
        True,
        "Numbers that are not values 3",
    ),
    Case(
        "table ID of another table",
        "no",
        lambda r: "Tabell 06856 viser dette [E1].",
        False,
        "Numbers that are not values 3",
    ),
    Case(
        "number from a label",
        "en",
        lambda r: f"People aged {r.english.dimensions[1].values[3].label} [E1].",
        True,
        "Numbers that are not values 4",
    ),
    Case(
        "label number with a unit",
        "en",
        lambda r: "16 per cent were young [E1].",
        False,
        "Numbers that are not values 4",
    ),
    Case(
        "list numbers are ignored",
        "no",
        lambda r: (
            f"1. Folketallet var {n(r.cell('2026'), 'no')} [E1].\n2. I 2025: {n(r.cell('2025'), 'no')} [E1]."
        ),
        True,
        "Numbers that are not values 5",
    ),
    Case(
        "table rows are blocks",
        "en",
        lambda r: f"| Year | People |\n| --- | --- |\n| 2026 | {n(r.cell('2026'), 'en')} [E1] |",
        True,
        "Input 3, table rows",
    ),
    Case(
        "table row without a citation",
        "en",
        lambda r: f"| Year | People |\n| --- | --- |\n| 2026 | {n(r.cell('2026'), 'en')} |",
        False,
        "Input 3, table rows",
    ),
    Case(
        "a tie rounds away from zero",
        "no",
        lambda r: f"Snittet var {n(r.step('mean', r.fish), 'no', 1)} kg [C2].",
        True,
        "Matching values 2, ties",
    ),
    Case(
        "a tie rounded toward zero",
        "no",
        lambda r: (
            f"Snittet var {format_number(round_away(r.step('mean', r.fish), 1) - Decimal('0.1'), 'no')} kg [C2]."
        ),
        False,
        "Matching values 2, ties",
    ),
    Case(
        "exponent notation",
        "en",
        lambda r: "The population was 5.6e6 [E1].",
        False,
        "Finding numbers 6",
    ),
    Case(
        "multiplying word",
        "no",
        lambda r: "Folketallet doblet seg [E1].",
        False,
        "Finding numbers 6",
    ),
]


@pytest.mark.parametrize("case", CASES, ids=[case.name for case in CASES])
def test_the_gate_reaches_the_decision_of_every_example(run: Run, case: Case) -> None:
    answer = case.answer(run)
    report = check(answer, case.language, run.evidence(case.language))
    assert report.passed is case.passes, (case.rule, answer, report.text())


def test_the_corpus_covers_every_rule_of_matching_and_kinds() -> None:
    rules = {case.rule.split(",")[0] for case in CASES}
    for section in (
        "Matching values 1",
        "Matching values 2",
        "Matching values 3",
        "Matching values 4",
    ):
        assert section in rules
    for item in range(1, 6):
        assert f"Numbers that are not values {item}" in rules


def test_the_same_answer_gets_the_same_report(run: Run) -> None:
    answer = CASES[0].answer(run) + "\n\n" + CASES[7].answer(run)
    reports: list[Report] = [check(answer, "no", run.evidence("no")) for _ in range(3)]
    assert reports[0] == reports[1] == reports[2]

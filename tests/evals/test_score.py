"""The scorer and its report, on example answers built from recorded fixture values.

Each case has the verdict it must get and the rule of spec 0005 it tests. Every number
that an answer must hold is formatted from the evidence by the project's formatter;
numbers that must not count are written by hand, as their case says. The expected
numbers come from the engine's results, never from a hand-written value.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import pytest
from answering import CONFIG, Evidences, evidences
from snapshotting import retrieve

from opencited.agent.answer import Answer, Refusal, RunUsage, answer_of, model_record, usage_of
from opencited.agent.run import Attempt, RunState
from opencited.calc.arithmetic import round_away
from opencited.calc.engine import Engine
from opencited.calc.plan import Plan
from opencited.calc.result import CalculationResult
from opencited.evals.report import Group, QuestionUse, Rate, report_of
from opencited.evals.score import (
    Category,
    Expectation,
    ExpectedNumber,
    Failure,
    NumberFound,
    Verdict,
    has_label,
    rounded,
    score,
)
from opencited.gate import CalculationEvidence, Evidence, SourceEvidence, check
from opencited.snapshots import SnapshotStore
from opencited.sources import Language
from opencited.values import format_number

# A number of the schema tests, which run no plan.
NUMBER = {"step": "after", "exact": "5627400", "kind": "amount", "decimals": 0}
USAGE = RunUsage(
    model_calls=4,
    tool_calls=5,
    input_tokens=20_000,
    output_tokens=700,
    seconds=Decimal("38.500"),
    cost=Decimal("0.005894"),
)


@dataclass(frozen=True)
class World:
    found: Evidences
    ranking: CalculationResult
    evidence: tuple[Evidence, ...]

    def population(self) -> ExpectedNumber:
        value = self.found.value("2026")
        return ExpectedNumber(step="after", exact=str(Fraction(value)), kind="amount", decimals=0)

    def growth(self, decimals: int = 2) -> ExpectedNumber:
        exact = self.found.calculation.step("growth").exact
        assert exact is not None
        return ExpectedNumber(step="growth", exact=exact, kind="per cent", decimals=decimals)

    def top(self) -> tuple[ExpectedNumber, str, Decimal]:
        cell = self.ranking.step("largest").ranked[0]
        number = ExpectedNumber(
            step="largest", exact=str(Fraction(cell.value)), kind="amount", decimals=0
        )
        return number, cell.labels[1], cell.value


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> World:
    store = SnapshotStore(tmp_path_factory.mktemp("snapshots"))
    found = evidences(store)
    snapshot_id = found.query.provenance.response_sha256
    ages = [value.code for value in found.query.dimensions[1].values if value.code != "999B"]
    step = {
        "name": "largest",
        "operation": "rank",
        "snapshot": snapshot_id,
        "selection": {
            "Kjonn": ["0"],
            "Alder": ages,
            "ContentsCode": ["Personer"],
            "Tid": ["2026"],
        },
        "by": "Alder",
        "order": "highest",
        "count": 1,
    }
    ranking = Engine(store).run(Plan.model_validate_json(json.dumps({"steps": [step]})))
    evidence = (*found.evidence, CalculationEvidence("C2", ranking))
    return World(found, ranking, evidence)


def answered(
    world: World, text: str, language: Language, attempts: tuple[Attempt, ...] = ("passed",)
) -> Answer:
    return answered_from(world.evidence, text, language, attempts)


def answered_from(
    evidence: Sequence[Evidence],
    text: str,
    language: Language,
    attempts: tuple[Attempt, ...] = ("passed",),
) -> Answer:
    return answer_of(
        question="A question of the test",
        language=language,
        text=text,
        report=check(text, language, evidence),
        attempts=attempts,
        # Each attempt but the last went back to the model.
        retries=len(attempts) - 1,
        evidence=evidence,
        model=model_record(CONFIG, ["a-model-2026-10-01"]),
        prompt_version="1",
        usage=USAGE,
    )


def refused(language: Language, reason: str = "no data") -> Refusal:
    return Refusal.model_validate(
        {
            "question": "A question of the test",
            "language": language,
            "reason": reason,
            "detail": "the model refused",
            "limit": None,
            "explanation": None,
            "report": None,
            "attempts": (),
            "retries": 0,
            "model": model_record(CONFIG, ["a-model-2026-10-01"]),
            "prompt_version": "1",
            "usage": usage_of(
                RunState(model_calls=2, input_tokens=4_000, output_tokens=90), CONFIG.prices
            ),
        }
    )


def expect(
    question_id: str,
    language: Language,
    category: Category,
    numbers: tuple[ExpectedNumber, ...] = (),
    label: str | None = None,
) -> Expectation:
    outcome = "refusal" if category in ("unanswerable", "out of scope") else "answer"
    return Expectation(
        question_id=question_id,
        language=language,
        category=category,
        outcome=outcome,
        numbers=numbers,
        label=label,
    )


def test_an_answer_with_the_expected_number_is_correct(world: World) -> None:
    value = format_number(world.found.value("2026"), "en")
    result = answered(world, f"In 2026, {value} people lived in Norway [E1].", "en")

    verdict = score(
        expect("lookup-1", "en", "lookup", (world.population(),)), result, world.evidence
    )

    assert verdict == Verdict(
        question_id="lookup-1",
        language="en",
        category="lookup",
        expected="answer",
        outcome="answer",
        correct=True,
        reason=None,
        passes_gate=True,
        numbers=(NumberFound(step="after", text=value),),
        label_found=None,
        attempts=("passed",),
        retries=0,
        usage=USAGE,
    )


def test_a_number_with_a_scale_word_shows_too_few_decimals(world: World) -> None:
    # 5.6 million fits the value under the gate's rule, but shows it to a tenth of a
    # million, and the question asks for whole persons. Written by hand on purpose.
    result = answered(world, "About 5.6 million people lived in Norway in 2026 [E1].", "en")

    verdict = score(
        expect("lookup-2", "en", "lookup", (world.population(),)), result, world.evidence
    )

    assert verdict.passes_gate is True
    assert verdict.numbers == (NumberFound(step="after", text=None),)
    assert verdict.correct is False


@pytest.mark.parametrize(
    ("decimals", "required", "correct"), [(2, 2, True), (3, 2, True), (1, 2, False), (0, 0, True)]
)
def test_a_change_needs_its_decimals_or_more(
    world: World, decimals: int, required: int, correct: bool
) -> None:
    exact = Fraction(world.growth().exact)
    written = format_number(round_away(exact, decimals), "no")
    result = answered(world, f"Folketallet økte med {written} prosent fra 2025 [C1] [E1].", "no")

    verdict = score(
        expect("change-1", "no", "change", (world.growth(required),)), result, world.evidence
    )

    assert verdict.passes_gate is True
    assert verdict.correct is correct
    # A number's text holds its unit word.
    assert verdict.numbers[0].text == (f"{written} prosent" if correct else None)


def test_a_per_cent_value_needs_a_per_cent_word(world: World) -> None:
    written = format_number(world.found.step("growth"), "no")
    result = answered(world, f"Folketallet økte med {written} fra 2025 [C1] [E1].", "no")

    verdict = score(expect("change-2", "no", "change", (world.growth(),)), result, world.evidence)

    assert verdict.numbers == (NumberFound(step="growth", text=None),)
    assert verdict.passes_gate is False
    assert verdict.correct is False


def test_an_answer_that_fails_the_gate_again_is_wrong(world: World) -> None:
    value = format_number(world.found.value("2026"), "en")
    # The 42 is in no result, written by hand so that the gate blocks it.
    text = f"In 2026, {value} people lived in Norway, and 42 of them were counted twice [E1]."
    result = answered(world, text, "en")

    verdict = score(
        expect("lookup-3", "en", "lookup", (world.population(),)), result, world.evidence
    )

    assert verdict.numbers == (NumberFound(step="after", text=value),)
    assert verdict.passes_gate is False
    assert verdict.correct is False


@pytest.mark.parametrize(("upper", "correct"), [(True, True), (False, False)])
def test_a_ranking_answer_needs_the_label_in_any_case(
    world: World, upper: bool, correct: bool
) -> None:
    number, label, value = world.top()
    # The label in capitals, or a text without it.
    shown = label.upper() if upper else "the largest group"
    text = f"The largest age group in 2026 was {shown} with {format_number(value, 'en')} people [C2] [E1]."
    result = answered(world, text, "en")

    verdict = score(expect("ranking-1", "en", "ranking", (number,), label), result, world.evidence)

    assert verdict.label_found is correct
    assert verdict.correct is correct
    assert verdict.numbers[0].text == format_number(value, "en")


def test_a_question_that_should_be_refused_is_correct_when_refused(world: World) -> None:
    verdict = score(expect("unanswerable-1", "no", "unanswerable"), refused("no"), world.evidence)

    assert (verdict.outcome, verdict.correct, verdict.reason) == ("refusal", True, "no data")
    assert verdict.usage is not None


def test_an_answer_to_a_question_that_should_be_refused_is_wrong(world: World) -> None:
    value = format_number(world.found.value("2026"), "no")
    result = answered(world, f"I 2026 bodde det {value} personer i Norge [E1].", "no")

    verdict = score(expect("out-of-scope-1", "no", "out of scope"), result, world.evidence)

    assert (verdict.outcome, verdict.correct, verdict.passes_gate) == ("answer", False, True)


def test_a_refusal_of_a_question_with_an_answer_is_wrong(world: World) -> None:
    verdict = score(
        expect("lookup-4", "no", "lookup", (world.population(),)),
        refused("no", "gate blocked"),
        world.evidence,
    )

    assert (verdict.outcome, verdict.correct, verdict.reason) == ("refusal", False, "gate blocked")


def test_a_run_that_failed_is_wrong_and_keeps_its_message(world: World) -> None:
    failure = Failure(message="the endpoint could not be reached: Connection error.", usage=None)

    verdict = score(expect("sum-1", "en", "sum", (world.population(),)), failure, world.evidence)

    assert (verdict.outcome, verdict.correct, verdict.reason) == ("failure", False, failure.message)


def test_the_same_answers_and_data_give_the_same_verdicts(world: World) -> None:
    value = format_number(world.found.value("2026"), "en")
    expectation = expect("lookup-5", "en", "lookup", (world.population(),))

    verdicts = [
        score(
            expectation,
            answered(world, f"In 2026, {value} people lived in Norway [E1].", "en"),
            world.evidence,
        )
        for _ in range(3)
    ]

    assert verdicts[0] == verdicts[1] == verdicts[2]


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"category": "lookup", "outcome": "answer"}, "expects at least one number"),
        ({"category": "lookup", "outcome": "refusal"}, "expects the other outcome"),
        ({"category": "unanswerable", "outcome": "answer"}, "expects the other outcome"),
        (
            {"category": "ranking", "outcome": "answer", "numbers": (NUMBER,)},
            "a ranking question expects a label",
        ),
        (
            {"category": "lookup", "outcome": "answer", "numbers": (NUMBER,), "label": "Oslo"},
            "a ranking question expects a label, and no other",
        ),
        (
            {"category": "ranking", "outcome": "answer", "numbers": (NUMBER,), "label": ""},
            "at least 1 character",
        ),
        (
            {"category": "unanswerable", "outcome": "refusal", "label": "Oslo"},
            "expects no number or label",
        ),
        (
            {"category": "lookup", "outcome": "answer", "question_id": "Lookup 1"},
            "String should match pattern",
        ),
    ],
)
def test_an_expectation_must_fit_its_outcome(fields: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        Expectation.model_validate({"question_id": "q-1", "language": "no", **fields})


def test_the_report_counts_by_category_language_and_outcome(world: World) -> None:
    value = format_number(world.found.value("2026"), "en")
    growth = format_number(world.found.step("growth"), "no")
    good = answered(
        world, f"In 2026, {value} people lived in Norway [E1].", "en", ("blocked", "passed")
    )
    rounded = answered(world, "About 5.6 million people lived in Norway in 2026 [E1].", "en")
    change = answered(
        world,
        f"Folketallet økte med {growth} prosent fra 2025 [C1] [E1].",
        "no",
        ("uncited", "blocked", "passed"),
    )
    verdicts = [
        score(expect("lookup-1", "en", "lookup", (world.population(),)), good, world.evidence),
        score(expect("lookup-2", "en", "lookup", (world.population(),)), rounded, world.evidence),
        score(expect("change-1", "no", "change", (world.growth(),)), change, world.evidence),
        score(
            expect("lookup-3", "no", "lookup", (world.population(),)),
            refused("no", "limit"),
            world.evidence,
        ),
        score(expect("unanswerable-1", "no", "unanswerable"), refused("no"), world.evidence),
        score(expect("out-of-scope-1", "en", "out of scope"), good, world.evidence),
        score(
            expect("sum-1", "en", "sum", (world.population(),)),
            Failure(message="stopped", usage=None),
            world.evidence,
        ),
    ]

    report = report_of(verdicts)

    assert report.questions == 7
    assert report.correct == Rate(count=3, total=7, rate=Decimal("0.4286"))
    assert report.by_category == (
        Group(name="lookup", correct=Rate(count=1, total=3, rate=Decimal("0.3333"))),
        Group(name="change", correct=Rate(count=1, total=1, rate=Decimal("1.0000"))),
        Group(name="sum", correct=Rate(count=0, total=1, rate=Decimal("0.0000"))),
        Group(name="unanswerable", correct=Rate(count=1, total=1, rate=Decimal("1.0000"))),
        Group(name="out of scope", correct=Rate(count=0, total=1, rate=Decimal("0.0000"))),
    )
    assert report.by_language == (
        Group(name="no", correct=Rate(count=2, total=3, rate=Decimal("0.6667"))),
        Group(name="en", correct=Rate(count=1, total=4, rate=Decimal("0.2500"))),
    )
    assert report.refused_with_answer == Rate(count=1, total=5, rate=Decimal("0.2000"))
    assert report.answered_should_refuse == Rate(count=1, total=2, rate=Decimal("0.5000"))
    assert report.failures == 1
    # Runs with a checked reply: lookup-1, lookup-2, change-1 and out-of-scope-1.
    assert report.first_attempt_blocked == Rate(count=2, total=4, rate=Decimal("0.5000"))
    assert report.first_attempt_uncited == Rate(count=1, total=4, rate=Decimal("0.2500"))
    assert report.retries == 1 + 0 + 2 + 1
    assert report.usage[0] == QuestionUse(
        question_id="lookup-1", tokens=20_700, cost=Decimal("0.005894"), seconds=Decimal("38.500")
    )
    assert report.usage[-1] == QuestionUse(
        question_id="sum-1", tokens=None, cost=None, seconds=None
    )
    assert report_of(verdicts) == report


def test_a_rate_of_nothing_is_none() -> None:
    report = report_of([])

    assert report.correct == Rate(count=0, total=0, rate=None)
    assert report.by_category == ()


@pytest.mark.parametrize(
    ("label", "text", "found"),
    [
        ("1 years", "The group 21 years was largest.", False),
        ("Trøndelag", "Sør-Trøndelag had the most.", False),
        ("Trøndelag", "Trøndelag had the most.", True),
        ("20-24 years", "The group 20\u201324 years was largest.", True),
        ("65 år", "Gruppen 65\u00a0år var størst.", True),
        ("Ålesund", "A\u030alesund had the most.", True),
        ("A\u030alesund", "Ålesund had the most.", True),
        ("Oslo", "Osloregionen had the most.", False),
        ("Oslo", "<!-- Oslo --> Bergen had the most.", False),
        # The documented limit: a label that begins another one still counts.
        ("Troms", "Troms og Finnmark had the most.", True),
        # A genitive may follow a label; the first is from an answer of the published
        # run of set v2 on 2026-10-18.
        ("5-9 år", "Flest skilsmisser kom etter 5\u20139 års ekteskap.", True),
        ("Oslo", "Oslos befolkning vokste.", True),
        ("Oslo", "Oslo's population grew.", True),
        ("Oslo", "Oslo\u2019s population grew.", True),
        ("Troms", "Troms' befolkning vokste.", True),
        ("Ås", "Åssiden hadde flest.", False),
    ],
)
def test_a_label_counts_only_as_a_whole_in_the_shown_text(
    label: str, text: str, found: bool
) -> None:
    assert has_label(text, label) is found


CPI = {"ContentsCode": ["KpiIndMnd"], "Tid": ["2025M09", "2026M09"]}


@pytest.mark.parametrize(
    ("period", "month", "written", "held"),
    [
        # SSB gives the index as 104 in a unit of one decimal, so 104 leaves out a zero.
        ("2026M09", "September 2026", "104", True),
        ("2026M09", "September 2026", "104.0", True),
        # 101 fits 100.6 under the gate's rule and leaves out a 6.
        ("2025M09", "September 2025", "101", False),
        ("2025M09", "September 2025", "100.6", True),
    ],
)
def test_a_number_may_leave_out_decimals_that_are_zeros(
    period: str, month: str, written: str, held: bool
) -> None:
    result = retrieve("14710", CPI, "en").result
    value = next(cell.value for cell in result.observations if cell.codes[1] == period)
    assert value is not None
    expected = ExpectedNumber(step="index", exact=str(Fraction(value)), kind="amount", decimals=1)
    evidence = [SourceEvidence("E1", result)]
    answer = answered_from(evidence, f"The index was {written} in {month} [E1].", "en")

    verdict = score(expect("lookup-8", "en", "lookup", (expected,)), answer, evidence)

    assert verdict.passes_gate is True
    assert verdict.numbers == (NumberFound(step="index", text=written if held else None),)
    assert verdict.correct is held


@pytest.mark.parametrize(("written", "held"), [("3.38", True), ("3.4", False), ("3.380", True)])
def test_a_change_may_leave_out_decimals_that_round_to_zeros(
    tmp_path: Path, written: str, held: bool
) -> None:
    # The index rose from 100.6 to 104, by 3.3797... per cent, which is 3.380 to three
    # decimals.
    store = SnapshotStore(tmp_path)
    retrieval = retrieve("14710", CPI, "en")
    snapshot = store.write(retrieval).name
    codes = {"ContentsCode": "KpiIndMnd"}
    steps = [
        {
            "name": "before",
            "operation": "value",
            "snapshot": snapshot,
            "codes": {**codes, "Tid": "2025M09"},
        },
        {
            "name": "after",
            "operation": "value",
            "snapshot": snapshot,
            "codes": {**codes, "Tid": "2026M09"},
        },
        {
            "name": "growth",
            "operation": "change_percent",
            "old": "before",
            "new": "after",
            "decimals": 3,
        },
    ]
    calculation = Engine(store).run(Plan.model_validate_json(json.dumps({"steps": steps})))
    exact = calculation.step("growth").exact
    assert exact is not None
    expected = ExpectedNumber(step="growth", exact=exact, kind="per cent", decimals=3)
    evidence: list[Evidence] = [
        SourceEvidence("E1", retrieval.result),
        CalculationEvidence("C1", calculation),
    ]
    text = f"The index rose by {written} per cent from September 2025 to September 2026 [C1] [E1]."

    verdict = score(
        expect("change-5", "en", "change", (expected,)),
        answered_from(evidence, text, "en"),
        evidence,
    )

    assert verdict.passes_gate is True
    assert verdict.numbers == (
        NumberFound(step="growth", text=f"{written} per cent" if held else None),
    )


@pytest.mark.parametrize(
    ("value", "places", "result"),
    [
        (Fraction("104.04"), 1, Fraction("104.0")),
        (Fraction(5627400), -3, Fraction(5627000)),
        (Fraction(2500), -3, Fraction(3000)),
        (Fraction(-2500), -3, Fraction(-3000)),
    ],
)
def test_a_value_rounds_to_any_place_with_ties_away_from_zero(
    value: Fraction, places: int, result: Fraction
) -> None:
    assert rounded(value, places) == result


def test_every_expected_number_must_be_in_the_answer(world: World) -> None:
    value = format_number(world.found.value("2026"), "no")
    result = answered(world, f"I 2026 bodde det {value} personer i Norge [E1].", "no")
    expectation = expect("change-3", "no", "change", (world.population(), world.growth()))

    verdict = score(expectation, result, world.evidence)

    assert [item.text for item in verdict.numbers] == [value, None]
    assert verdict.correct is False


def test_a_number_with_the_other_sign_does_not_hold_the_value(world: World) -> None:
    written = format_number(-world.found.step("growth"), "no")
    result = answered(world, f"Folketallet endret seg med {written} prosent [C1] [E1].", "no")

    verdict = score(expect("change-4", "no", "change", (world.growth(),)), result, world.evidence)

    assert verdict.numbers == (NumberFound(step="growth", text=None),)
    assert verdict.correct is False


def test_another_cited_value_is_not_the_expected_one(world: World) -> None:
    earlier = format_number(world.found.value("2025"), "no")
    result = answered(world, f"Det bodde {earlier} personer i Norge [E1].", "no")

    verdict = score(
        expect("lookup-6", "no", "lookup", (world.population(),)), result, world.evidence
    )

    assert verdict.passes_gate is True
    assert verdict.numbers == (NumberFound(step="after", text=None),)
    assert verdict.correct is False


def test_periods_and_numbers_in_comments_are_not_values(world: World) -> None:
    value = format_number(world.found.value("2026"), "no")
    # An expected value equal to a year, written by hand to show that a period that the
    # gate passes is not a value.
    year = ExpectedNumber(step="year", exact="2026", kind="amount", decimals=0)
    result = answered(world, f"Tallet gjelder 2026 [E1]. <!-- {value} [E1] -->", "no")

    verdict = score(
        expect("lookup-7", "no", "lookup", (year, world.population())), result, world.evidence
    )

    assert verdict.passes_gate is True
    assert verdict.numbers == (
        NumberFound(step="year", text=None),
        NumberFound(step="after", text=None),
    )


def test_the_counts_do_not_depend_on_the_order_of_the_verdicts(world: World) -> None:
    value = format_number(world.found.value("2026"), "en")
    good = answered(
        world, f"In 2026, {value} people lived in Norway [E1].", "en", ("blocked", "passed")
    )
    verdicts = [
        score(expect("lookup-1", "en", "lookup", (world.population(),)), good, world.evidence),
        score(expect("unanswerable-1", "no", "unanswerable"), refused("no"), world.evidence),
        score(
            expect("change-1", "no", "change", (world.growth(),)),
            refused("no", "limit"),
            world.evidence,
        ),
    ]

    forward, backward = report_of(verdicts), report_of(verdicts[::-1])

    assert forward.model_copy(update={"usage": ()}) == backward.model_copy(update={"usage": ()})
    assert forward.usage == backward.usage[::-1]


def test_a_refusal_keeps_its_attempts_and_retries(world: World) -> None:
    refusal = refused("no", "gate blocked").model_copy(
        update={"attempts": ("blocked", "uncited", "blocked"), "retries": 2}
    )

    verdict = score(
        expect("lookup-8", "no", "lookup", (world.population(),)), refusal, world.evidence
    )

    assert (verdict.attempts, verdict.retries) == (("blocked", "uncited", "blocked"), 2)


def test_a_number_that_the_gate_did_not_match_does_not_hold_the_value(world: World) -> None:
    value = format_number(world.found.value("2026"), "no")
    result = answered(world, f"I 2026 bodde det {value} personer i Norge.", "no")

    verdict = score(
        expect("lookup-9", "no", "lookup", (world.population(),)), result, world.evidence
    )

    assert verdict.passes_gate is False
    assert verdict.numbers == (NumberFound(step="after", text=None),)


def test_a_failed_run_keeps_its_attempts_and_the_report_counts_recorded_retries(
    world: World,
) -> None:
    failure = Failure(message="stopped", usage=None, attempts=("blocked",), retries=1)
    refusal = refused("no").model_copy(update={"attempts": ("blocked",), "retries": 1})

    verdicts = [
        score(expect("sum-2", "en", "sum", (world.population(),)), failure, world.evidence),
        score(expect("lookup-10", "no", "lookup", (world.population(),)), refusal, world.evidence),
    ]

    assert (verdicts[0].attempts, verdicts[0].retries) == (("blocked",), 1)
    # Each run sent its blocked reply back once, and then stopped without a new check.
    assert report_of(verdicts).retries == 2
    assert report_of(verdicts).first_attempt_blocked == Rate(
        count=2, total=2, rate=Decimal("1.0000")
    )


EMPLOYED = {
    "Alder": ["15-74"],
    "Kjonn": ["2", "1"],
    "ContentsCode": ["Sysselsatte"],
    "Tid": ["2026K2"],
}


@pytest.mark.parametrize(
    ("written", "decimals", "found"),
    [
        ("2 935 000", -3, True),
        ("2 935 tusen", -3, True),
        ("2,9 millioner", -3, False),
        ("2 935", -3, False),
        # To the hundred, 2 935 tusen leaves out a zero.
        ("2 935 tusen", -2, True),
    ],
)
def test_a_value_in_thousands_is_expected_in_persons_to_the_thousand(
    tmp_path: Path, written: str, decimals: int, found: bool
) -> None:
    # 03778 gives employment in 1 000 personer, and the question asks for whole thousands.
    store = SnapshotStore(tmp_path)
    retrieval = retrieve("03778", EMPLOYED, "no")
    snapshot = store.write(retrieval).name
    step = {"name": "employed", "operation": "sum", "snapshot": snapshot, "selection": EMPLOYED}
    plan = Plan.model_validate_json(json.dumps({"steps": [step]}))
    evidence: list[Evidence] = [
        SourceEvidence("E1", retrieval.result),
        CalculationEvidence("C1", Engine(store).run(plan)),
    ]
    expected = ExpectedNumber(step="employed", exact="2935000", kind="amount", decimals=decimals)
    text = f"I 2. kvartal 2026 var {written} personer sysselsatt [C1] [E1]."

    verdict = score(
        expect("sum-employed", "no", "sum", (expected,)),
        answered_from(evidence, text, "no"),
        evidence,
    )

    assert (verdict.numbers[0].text is not None) is found
    assert verdict.correct is found

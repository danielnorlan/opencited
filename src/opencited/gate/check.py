"""The evidence gate: every number in an answer against the results its block cites.

``check`` reads the numbers of an answer, splits the answer into blocks and matches
each number against the results that the block cites (spec 0003, tasks 3 and 4):

- A period passes when a cited query result selected it, a date when it is the last
  update or the retrieval date of a cited result or a day that a cited query result's
  label names in a year it selected, a day and month without a year when such a label
  names it, a five-digit table ID when it is the ID of a cited result, and a number
  from the title of a cited result or from the label of a value that the result
  selected when it has no unit, percent or scale word. For periods and dates, a cited
  calculation also counts as citing the query results its steps read, and it gives
  the numbers of the labels of the values they read.
- Every other number is a value. It must stand in a block that cites a result, and
  equal a value ``v`` of a cited result after ``v``, divided by any scale word, is
  rounded with ties away from zero to the decimals written. For a calculation, ``v``
  is the exact result before rounding.
- A number with a percent word matches only per cent values, one with a percentage
  point word only percentage point values, and any other number only values that are
  not per cent.

The report lists every number with the evidence it matched or the reason it failed.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from fractions import Fraction
from typing import Literal, cast
from zoneinfo import ZoneInfo

from pydantic import Field

from opencited.calc.arithmetic import round_away
from opencited.calc.engine import bound_codes, unit_named
from opencited.calc.result import CalculationResult, StepResult
from opencited.gate.blocks import blocks
from opencited.gate.numbers import (
    MONTHS_EN,
    MONTHS_NO,
    Language,
    Number,
    alternatives,
    normalise,
    read_numbers,
)
from opencited.model import Frozen
from opencited.sources import QueryResult, SearchResult, TableMetadata
from opencited.values import ExactDecimal

OSLO = ZoneInfo("Europe/Oslo")
MONTH_NUMBERS = {
    name.lower(): number
    for names in (MONTHS_NO, MONTHS_EN)
    for number, name in enumerate(names, start=1)
}
# A day and a month in a label: "1. januar", "1 January", or "1.1." as in
# "Population per 1.1.". English months need their capital, as in answers, which
# keeps "may" the verb out.
DAY_IN_WORDS = re.compile(
    rf"(?<![0-9.])([0-9]{{1,2}})\.?\s+((?ai:{alternatives(MONTHS_NO)})|{alternatives(MONTHS_EN)})\b"
)
DAY_IN_DIGITS = re.compile(r"(?<![0-9.])([0-9]{1,2})\.([0-9]{1,2})\.(?![0-9])")
# A week as SSB codes it, such as 2026U41.
WEEK_CODE = re.compile(r"[0-9]{4}U([0-9]{2})")
COMMENT = re.compile(r"<!--.*?(?:-->|$)", re.DOTALL)
SOURCE_ID = re.compile(r"E[1-9][0-9]*")
CALCULATION_ID = re.compile(r"C[1-9][0-9]*")

type UnitKind = Literal["amount", "per cent", "percentage points", "ratio"]
type FindingKind = Literal[
    "value", "period", "date", "table ID", "label number", "list number", "citation", "blocked"
]
type Outcome = Literal["matched", "passed", "ignored", "failed"]


@dataclass(frozen=True)
class SourceEvidence:
    """A source result of the run, with its evidence ID, such as ``E1``."""

    evidence_id: str
    result: QueryResult | TableMetadata | SearchResult

    def __post_init__(self) -> None:
        if not SOURCE_ID.fullmatch(self.evidence_id):
            raise ValueError(
                f"a source result's evidence ID is E and a number, not {self.evidence_id!r}"
            )


@dataclass(frozen=True)
class CalculationEvidence:
    """A calculation result of the run, with its evidence ID, such as ``C1``."""

    evidence_id: str
    result: CalculationResult

    def __post_init__(self) -> None:
        if not CALCULATION_ID.fullmatch(self.evidence_id):
            raise ValueError(
                f"a calculation's evidence ID is C and a number, not {self.evidence_id!r}"
            )


type Evidence = SourceEvidence | CalculationEvidence


class Match(Frozen):
    """A value of a cited result that a number fits: a cell, a step, or a ranked cell."""

    evidence: str
    cell: tuple[str, ...] | None = None
    step: str | None = None


class Finding(Frozen):
    """One number of the answer, and what the gate made of it.

    ``position`` and ``end`` are offsets in the answer as the reader normalised it, to
    Unicode NFC. ``value`` is the number as written, before its ``scale`` word, and
    ``unit`` its percent or percentage point word.
    """

    position: int
    end: int
    text: str
    kind: FindingKind
    value: ExactDecimal | None = None
    scale: int = 0
    unit: Literal["per cent", "percentage points"] | None = None
    outcome: Outcome
    evidence: str | None = None
    matches: tuple[Match, ...] = Field(
        default=(),
        description=(
            "Every value of the cited results that a matched number fits, the first one "
            "being the one that evidence and detail name. The gate checks which values a "
            "number may be, so it lists them all."
        ),
    )
    detail: str


class Report(Frozen):
    """Every number of an answer with its match or the reason it failed."""

    passed: bool
    findings: tuple[Finding, ...]

    def text(self) -> str:
        """Return the report as the agent sends it back to the model."""
        failed = [finding for finding in self.findings if finding.outcome == "failed"]
        if not failed:
            return f"The gate passed every number ({len(self.findings)} checked)."
        lines = [
            f"The gate blocked the answer: {len(failed)} of {len(self.findings)} numbers failed."
        ]
        lines += [
            f"- {finding.text!r} at {finding.position}: {finding.detail}" for finding in failed
        ]
        return "\n".join(lines)


# The reason for a year that fails as a value, which the agent's retry message reads.
UNSELECTED_YEAR = "a year that no cited result selected, and no value that a cited result holds"
# The reasons for a number in a block that cites nothing, which the retry message reads.
UNCITED_VALUE = "a value in a block that cites no result"
UNCITED_DATE = "a date in a block that cites no result"
UNCITED_PERIOD = "a period in a block that cites no result"
UNCITED_REASONS = (UNCITED_VALUE, UNCITED_DATE, UNCITED_PERIOD)
# The reason for a number outside every block, which only a code fence's own line
# can hold: no citation counts there.
NO_BLOCK = "a number outside every paragraph, list item and table row"


@dataclass(frozen=True)
class Candidate:
    """A value that a cited result holds, and where in the result it is."""

    exact: Fraction
    kind: UnitKind
    where: str
    cell: tuple[str, ...] | None = None
    step: str | None = None


@dataclass
class Holdings:
    """What the results that one block cites hold."""

    values: list[tuple[str, Candidate]] = field(default_factory=list[tuple[str, Candidate]])
    periods: dict[str, str] = field(default_factory=dict[str, str])
    dates: dict[date, str] = field(default_factory=dict[date, str])
    tables: dict[str, str] = field(default_factory=dict[str, str])
    labels: dict[str, str] = field(default_factory=dict[str, str])
    # Days that a query result gives for its figures: a day and month in a label, in a
    # year the result selected, such as 1 January 2026 for "Befolkning 1. januar".
    reference_dates: dict[date, str] = field(default_factory=dict[date, str])
    # The same days without a year, as (month, day), such as (1, 1).
    reference_days: dict[tuple[int, int], str] = field(default_factory=dict[tuple[int, int], str])
    # The numbers of the weeks that a query result selected, in any year, such as 41
    # for 2026U41, for a week written without a year.
    reference_weeks: dict[int, str] = field(default_factory=dict[int, str])

    def add(self, other: Holdings) -> None:
        """Add what another cited result holds; the first holder of each item is kept."""
        self.values.extend(other.values)
        for key, holder in other.tables.items():
            self.tables.setdefault(key, holder)
        for key, holder in other.labels.items():
            self.labels.setdefault(key, holder)
        self.add_times(other)

    def add_times(self, other: Holdings) -> None:
        """Add the periods and dates that another result holds, and nothing else."""
        for key, holder in other.periods.items():
            self.periods.setdefault(key, holder)
        for day, holder in other.dates.items():
            self.dates.setdefault(day, holder)
        for day, holder in other.reference_dates.items():
            self.reference_dates.setdefault(day, holder)
        for mark, holder in other.reference_days.items():
            self.reference_days.setdefault(mark, holder)
        for week, holder in other.reference_weeks.items():
            self.reference_weeks.setdefault(week, holder)


def day_months(text: str) -> set[tuple[int, int]]:
    """Return the days and months that a label names, such as (1, 1) for "1. januar"."""
    found: set[tuple[int, int]] = set()
    for match in DAY_IN_WORDS.finditer(text):
        found.add((MONTH_NUMBERS[match[2].lower()], int(match[1])))
    for match in DAY_IN_DIGITS.finditer(text):
        found.add((int(match[2]), int(match[1])))
    return {(month, day) for month, day in found if 1 <= month <= 12 and 1 <= day <= 31}


def read_codes(step: StepResult, result: QueryResult) -> tuple[tuple[str, str], ...]:
    """Return the dimension and code of each value that a step read in a query result.

    A step's first statement selects the cells it reads. A statement other than the one
    the engine writes for the result's dimensions gives nothing, so no label passes.
    """
    if not step.sql or not step.parameters:
        return ()
    dimensions = [dimension.code for dimension in result.dimensions]
    return bound_codes(step.sql[0], step.parameters[0], dimensions) or ()


def value_label_numbers(result: QueryResult) -> dict[tuple[str, str], list[str]]:
    """Return the numbers in the label of each value of a query result, apart from periods."""
    language = language_of(result.provenance.table_url)
    return {
        (dimension.code, value.code): label_numbers(value.label, language)
        for dimension in result.dimensions
        if not dimension.time
        for value in dimension.values
    }


def label_days(result: QueryResult) -> set[tuple[int, int]]:
    """Return the days and months that the labels of a query result's values name."""
    return {
        mark
        for dimension in result.dimensions
        for value in dimension.values
        for mark in day_months(value.label)
    }


def reference_dates(result: QueryResult) -> set[date]:
    """Return the dates that a query result gives as the days of its figures.

    The day comes from the label of a value the result selected. Titles are not read:
    no recorded query result's title names a day, and the gate blocks what it cannot
    trace to the result.
    """
    marks = label_days(result)
    years = {
        int(value.code)
        for dimension in result.dimensions
        if dimension.time
        for value in dimension.values
        if re.fullmatch(r"[0-9]{4}", value.code)
    }
    found: set[date] = set()
    for year in years:
        for month, day in marks:
            try:
                found.add(date(year, month, day))
            except ValueError:
                continue
    return found


def language_of(url: str) -> Language:
    """Return the language of a StatBank link, which has /en/ for English."""
    return "en" if "/en/" in url else "no"


def label_numbers(text: str, language: Language) -> list[str]:
    """Return the numbers in a title or a label as they are written there."""
    numbers = read_numbers(text, language)
    # The day of "Befolkning 1. januar" is a number of the label too.
    days = [str(number.day[1]) for number in numbers if number.day is not None]
    return [number.text for number in numbers if number.kind == "number"] + days


def days(moment: datetime) -> list[date]:
    """Return the date of a time in UTC and in Norwegian time."""
    return [moment.date(), moment.astimezone(OSLO).date()]


def value_of(number: Number) -> Decimal:
    """Return the value of a number token; dates, periods and blocked forms have none."""
    if number.value is None:
        raise ValueError(f"{number.text!r} has no value")
    return number.value


def held(evidence: Evidence) -> Holdings:
    """Return what one result holds, for every block that cites it."""
    holdings = Holdings()
    hold(holdings, evidence)
    return holdings


def hold(holdings: Holdings, evidence: Evidence) -> None:
    """Add what one cited result holds."""
    name = evidence.evidence_id
    if isinstance(evidence, CalculationEvidence):
        for step in evidence.result.steps:
            # A value in a unit such as 1000 personer is compared in persons (item 6).
            base = Fraction(10) ** step.unit.scale
            if step.exact is not None:
                where = f"step {step.name}"
                exact = Fraction(step.exact) * base
                candidate = Candidate(exact, step.unit.kind, where, step=step.name)
                holdings.values.append((name, candidate))
            for cell in step.ranked:
                where = f"step {step.name}, {', '.join(cell.codes)}"
                candidate = Candidate(
                    Fraction(cell.value) * base,
                    step.unit.kind,
                    where,
                    cell=cell.codes,
                    step=step.name,
                )
                holdings.values.append((name, candidate))
        return
    result = evidence.result
    if isinstance(result, QueryResult):
        language = language_of(result.provenance.table_url)
        units = {unit.code: unit.base for unit in result.units}
        contents = [
            index for index, dimension in enumerate(result.dimensions) if dimension.contents
        ]
        for cell in result.observations:
            if cell.value is None:
                continue
            base = units.get(cell.codes[contents[0]]) if contents else None
            unit = unit_named(base)
            where = f"cell {', '.join(cell.codes)}"
            exact = Fraction(cell.value) * Fraction(10) ** unit.scale
            candidate = Candidate(exact, unit.kind, where, cell=cell.codes)
            holdings.values.append((name, candidate))
        for dimension in result.dimensions:
            for value in dimension.values:
                if dimension.time:
                    # A period's label is its code, which counts as a period, not a label.
                    holdings.periods.setdefault(value.code, name)
                    week = WEEK_CODE.fullmatch(value.code)
                    if week is not None:
                        holdings.reference_weeks.setdefault(int(week[1]), name)
                    continue
                for number in label_numbers(value.label, language):
                    holdings.labels.setdefault(number, name)
        for number in label_numbers(result.title, language):
            holdings.labels.setdefault(number, name)
        holdings.tables.setdefault(result.table_id, name)
        for day in [*days(result.updated), *days(result.provenance.retrieved_at)]:
            holdings.dates.setdefault(day, name)
        for day in sorted(reference_dates(result)):
            holdings.reference_dates.setdefault(day, name)
        for mark in sorted(label_days(result)):
            holdings.reference_days.setdefault(mark, name)
    elif isinstance(result, TableMetadata):
        language = language_of(result.url)
        holdings.tables.setdefault(result.table_id, name)
        for day in days(result.updated):
            holdings.dates.setdefault(day, name)
        for number in label_numbers(result.title, language):
            holdings.labels.setdefault(number, name)
    else:
        for table in result.tables:
            holdings.tables.setdefault(table.table_id, name)
            for day in days(table.updated):
                holdings.dates.setdefault(day, name)
            for number in label_numbers(table.title, language_of(table.url)):
                holdings.labels.setdefault(number, name)


def decimals(value: Decimal) -> int:
    return max(0, -int(value.as_tuple().exponent))


def fits(number: Number, candidate: Candidate) -> bool:
    """Return whether a value matches a candidate after the gate's one rounding."""
    value = value_of(number)
    target = candidate.exact / 10**number.scale
    return round_away(target, decimals(value)) == value


def allowed(number: Number, kind: UnitKind) -> bool:
    if number.unit == "per cent":
        return kind == "per cent"
    if number.unit == "percentage points":
        return kind == "percentage points"
    return kind != "per cent"


def fits_value(number: Number, exact: Fraction, kind: UnitKind) -> bool:
    """Return whether a number may be a value, by the gate's rules for unit and rounding.

    The evaluation suite's scorer reads expected numbers with this rule.
    """
    return (
        number.value is not None
        and allowed(number, kind)
        and fits(number, Candidate(exact, kind, ""))
    )


def why_not(number: Number, holdings: Holdings) -> str:
    """Return the most helpful reason why a value matched no cited value."""
    candidates = [candidate for _, candidate in holdings.values]
    if any(fits(number, candidate) for candidate in candidates):
        kinds = sorted({candidate.kind for candidate in candidates if fits(number, candidate)})
        return (
            f"a cited value has this number, but its unit is {', '.join(kinds)}, which the "
            f"number's {number.unit or 'lack of a percent word'} does not fit"
        )
    negated = number.model_copy(update={"value": -value_of(number)})
    if any(fits(negated, candidate) for candidate in candidates):
        return "a cited value has this number with the other sign"
    digits = number.text.strip()
    if number.unit is None and number.scale == 0 and re.fullmatch(r"[0-9]{4}", digits):
        return UNSELECTED_YEAR
    if number.unit is None and number.scale == 0 and re.fullmatch(r"[0-9]{5}", digits):
        return "the ID of no cited table, and no value that a cited result holds"
    return "no value of the cited results gives this number when rounded to the decimals written"


def finding(
    number: Number, kind: FindingKind, outcome: Outcome, detail: str, evidence: str | None = None
) -> Finding:
    return Finding(
        position=number.position,
        end=number.end,
        text=number.text,
        kind=kind,
        value=number.value,
        scale=number.scale,
        unit=number.unit,
        outcome=outcome,
        evidence=evidence,
        detail=detail,
    )


def outside(number: Number) -> Finding:
    """Decide a number that stands in no block: it fails, whatever it cites.

    A blocking form keeps its own reason, which names the form to write instead.
    """
    if number.kind == "blocked":
        return judge(number, Holdings(), False)
    kind: FindingKind = (
        "period"
        if number.kind in ("period", "week")
        else "date"
        if number.kind in ("date", "day")
        else "value"
    )
    return finding(number, kind, "failed", NO_BLOCK)


def judge(number: Number, holdings: Holdings, cited: bool) -> Finding:
    """Decide one number of a block whose cited results hold ``holdings``."""
    # A block that cites nothing holds nothing, and the reason says so, so that the
    # retry message asks for a citation rather than for another date or period.
    if not cited and number.kind in ("date", "day", "week", "period"):
        if number.kind in ("week", "period"):
            return finding(number, "period", "failed", UNCITED_PERIOD)
        return finding(number, "date", "failed", UNCITED_DATE)
    match number.kind:
        case "list number":
            return finding(number, "list number", "ignored", "the number of a list item")
        case "blocked":
            return finding(number, "blocked", "failed", number.reason or "a blocking form")
        case "date":
            day = number.date
            source = holdings.dates.get(day) if day is not None else None
            if source is not None:
                return finding(
                    number,
                    "date",
                    "passed",
                    "the update or retrieval date of a cited result",
                    source,
                )
            source = holdings.reference_dates.get(day) if day is not None else None
            if source is not None:
                return finding(
                    number,
                    "date",
                    "passed",
                    "a day that a cited query result's label names, in a year it selected",
                    source,
                )
            return finding(
                number,
                "date",
                "failed",
                "a date that no cited result has as its update or retrieval date, or as the "
                "day of its figures",
            )
        case "day":
            source = holdings.reference_days.get(number.day) if number.day is not None else None
            if source is None:
                return finding(
                    number,
                    "date",
                    "failed",
                    "a day and month that no cited query result's label names",
                )
            return finding(
                number,
                "date",
                "passed",
                "a day and month that a cited query result's label names",
                source,
            )
        case "week":
            source = holdings.reference_weeks.get(number.week or 0)
            if source is None:
                return finding(
                    number,
                    "period",
                    "failed",
                    f"week {number.week}, which no cited query result selected in a year",
                )
            return finding(
                number,
                "period",
                "passed",
                f"week {number.week}, which a cited query result selected in a year",
                source,
            )
        case "period":
            source = holdings.periods.get(number.period or "")
            if source is None:
                return finding(
                    number,
                    "period",
                    "failed",
                    f"the period {number.period}, which no cited result selected",
                )
            return finding(
                number,
                "period",
                "passed",
                f"the period {number.period}, which a cited result selected",
                source,
            )
        case _:
            pass
    value_of(number)
    if not cited:
        return finding(number, "value", "failed", UNCITED_VALUE)
    plain = number.unit is None and number.scale == 0
    digits = number.text.strip()
    if plain and re.fullmatch(r"[0-9]{4}", digits) and digits in holdings.periods:
        return finding(
            number,
            "period",
            "passed",
            f"the year {digits}, which a cited result selected",
            holdings.periods[digits],
        )
    if plain and re.fullmatch(r"[0-9]{5}", digits) and digits in holdings.tables:
        return finding(
            number, "table ID", "passed", f"the ID of table {digits}", holdings.tables[digits]
        )
    fitting = [
        (name, candidate)
        for name, candidate in holdings.values
        if allowed(number, candidate.kind) and fits(number, candidate)
    ]
    if fitting:
        name, candidate = fitting[0]
        matches = tuple(
            Match(evidence=holder, cell=item.cell, step=item.step) for holder, item in fitting
        )
        matched = finding(number, "value", "matched", candidate.where, name)
        return matched.model_copy(update={"matches": matches})
    if plain and digits in holdings.labels:
        return finding(
            number,
            "label number",
            "passed",
            "a number written as in a title or a label of a cited result",
            holdings.labels[digits],
        )
    return finding(number, "value", "failed", why_not(number, holdings))


def hidden_spans(text: str) -> list[tuple[int, int]]:
    """Return the spans of HTML comments, whose markers are not shown and cite nothing."""
    return [match.span() for match in COMMENT.finditer(text)]


def cited_ids(answer: str, language: Language) -> list[str]:
    """Return the evidence IDs that an answer's shown markers cite, in order of first use.

    These are the markers that the gate counts: a marker in an HTML comment cites
    nothing.
    """
    text = normalise(answer)
    hidden = hidden_spans(text)
    names = [
        number.text[1:-1]
        for number in read_numbers(text, language)
        if number.kind == "marker"
        and not any(start <= number.position < end for start, end in hidden)
    ]
    return list(dict.fromkeys(names))


def check(answer: str, language: Language, evidence: Sequence[Evidence]) -> Report:
    """Return the gate's report on an answer and the evidence of its run."""
    text = normalise(answer)
    known = {item.evidence_id: item for item in evidence}
    if len(known) != len(evidence):
        raise ValueError("two results of the run have the same evidence ID")
    numbers = read_numbers(text, language)
    # A marker in an HTML comment is not shown, so it cites nothing.
    hidden = hidden_spans(text)
    holdings_of = {name: held(item) for name, item in known.items()}
    # For periods and dates, a calculation also counts as citing every query result
    # that its steps read (spec 0003, Numbers that are not values, item 6).
    # The same response fetched twice gives two results with one hash and two retrieval
    # dates, so a hash keeps every result that has it.
    responses: dict[str, list[str]] = {}
    for name, item in known.items():
        if isinstance(item, SourceEvidence) and isinstance(item.result, QueryResult):
            responses.setdefault(item.result.provenance.response_sha256, []).append(name)
    # The label numbers of each value of a query result that a calculation read, read
    # once for all the steps of all calculations.
    value_numbers: dict[str, dict[tuple[str, str], list[str]]] = {}
    for name, item in known.items():
        if isinstance(item, CalculationEvidence):
            for use in item.result.snapshots:
                for source in responses.get(use.provenance.response_sha256, []):
                    holdings_of[name].add_times(holdings_of[source])
            # A label number passes beside a calculation when one of its steps read the
            # value that the label names (spec 0003, Numbers that are not values, item 7).
            for step in item.result.steps:
                for source in responses.get(step.snapshot or "", []):
                    result = cast("QueryResult", known[source].result)
                    if source not in value_numbers:
                        value_numbers[source] = value_label_numbers(result)
                    for value in read_codes(step, result):
                        for number in value_numbers[source].get(value, []):
                            holdings_of[name].labels.setdefault(number, source)
    findings: list[Finding] = []
    found = blocks(text)
    for block in found:
        inside = [number for number in numbers if block.start <= number.position < block.end]
        holdings = Holdings()
        cited = False
        for number in inside:
            if number.kind != "marker":
                continue
            if any(start <= number.position < end for start, end in hidden):
                continue
            name = number.text[1:-1]
            item = known.get(name)
            if item is None:
                findings.append(
                    finding(
                        number,
                        "citation",
                        "failed",
                        f"a citation of {name}, which the run has no result for",
                    )
                )
                continue
            cited = True
            holdings.add(holdings_of[item.evidence_id])
        findings += [judge(number, holdings, cited) for number in inside if number.kind != "marker"]
    # A number on a code fence's own line stands in no block that cites a result, so it
    # fails (spec 0003, Matching values, item 1).
    findings += [
        outside(number)
        for number in numbers
        if number.kind != "marker"
        and not any(block.start <= number.position < block.end for block in found)
    ]
    findings.sort(key=lambda item: (item.position, item.end))
    return Report(
        passed=all(item.outcome != "failed" for item in findings), findings=tuple(findings)
    )

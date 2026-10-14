"""The evidence gate: every number in an answer against the results its block cites.

``check`` reads the numbers of an answer, splits the answer into blocks and matches
each number against the results that the block cites (spec 0003, tasks 3 and 4):

- A period passes when a cited query result selected it, a date when it is the last
  update or the retrieval date of a cited result, a five-digit table ID when it is the
  ID of a cited result, and a number from the title of a cited result or from the label
  of a value that the result selected when it has no unit, percent or scale word.
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
from typing import Literal
from zoneinfo import ZoneInfo

from opencited.calc.arithmetic import round_away
from opencited.calc.engine import unit_named
from opencited.calc.result import CalculationResult
from opencited.gate.blocks import blocks
from opencited.gate.numbers import Language, Number, normalise, read_numbers
from opencited.model import Frozen
from opencited.sources import QueryResult, SearchResult, TableMetadata
from opencited.values import ExactDecimal

OSLO = ZoneInfo("Europe/Oslo")
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


@dataclass(frozen=True)
class Candidate:
    """A value that a cited result holds, and where in the result it is."""

    exact: Fraction
    kind: UnitKind
    where: str


@dataclass
class Holdings:
    """What the results that one block cites hold."""

    values: list[tuple[str, Candidate]] = field(default_factory=list[tuple[str, Candidate]])
    periods: dict[str, str] = field(default_factory=dict[str, str])
    dates: dict[date, str] = field(default_factory=dict[date, str])
    tables: dict[str, str] = field(default_factory=dict[str, str])
    labels: dict[str, str] = field(default_factory=dict[str, str])

    def add(self, other: Holdings) -> None:
        """Add what another cited result holds; the first holder of each item is kept."""
        self.values.extend(other.values)
        for mine, theirs in (
            (self.periods, other.periods),
            (self.tables, other.tables),
            (self.labels, other.labels),
        ):
            for key, holder in theirs.items():
                mine.setdefault(key, holder)
        for day, holder in other.dates.items():
            self.dates.setdefault(day, holder)


def language_of(url: str) -> Language:
    """Return the language of a StatBank link, which has /en/ for English."""
    return "en" if "/en/" in url else "no"


def label_numbers(text: str, language: Language) -> list[str]:
    """Return the numbers in a title or a label as they are written there."""
    return [number.text for number in read_numbers(text, language) if number.kind == "number"]


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
            if step.exact is not None:
                holdings.values.append(
                    (name, Candidate(Fraction(step.exact), step.unit.kind, f"step {step.name}"))
                )
            for cell in step.ranked:
                where = f"step {step.name}, {', '.join(cell.codes)}"
                holdings.values.append(
                    (name, Candidate(Fraction(cell.value), step.unit.kind, where))
                )
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
            kind = unit_named(base).kind
            holdings.values.append(
                (name, Candidate(Fraction(cell.value), kind, f"cell {', '.join(cell.codes)}"))
            )
        for dimension in result.dimensions:
            for value in dimension.values:
                if dimension.time:
                    # A period's label is its code, which counts as a period, not a label.
                    holdings.periods.setdefault(value.code, name)
                    continue
                for number in label_numbers(value.label, language):
                    holdings.labels.setdefault(number, name)
        for number in label_numbers(result.title, language):
            holdings.labels.setdefault(number, name)
        holdings.tables.setdefault(result.table_id, name)
        for day in [*days(result.updated), *days(result.provenance.retrieved_at)]:
            holdings.dates.setdefault(day, name)
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
        return "a year that no cited result selected, and no value that a cited result holds"
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


def judge(number: Number, holdings: Holdings, cited: bool) -> Finding:
    """Decide one number of a block whose cited results hold ``holdings``."""
    match number.kind:
        case "list number":
            return finding(number, "list number", "ignored", "the number of a list item")
        case "blocked":
            return finding(number, "blocked", "failed", number.reason or "a blocking form")
        case "date":
            source = holdings.dates.get(number.date) if number.date is not None else None
            if source is None:
                return finding(
                    number,
                    "date",
                    "failed",
                    "a date that no cited result has as its update or retrieval date",
                )
            return finding(
                number, "date", "passed", "the update or retrieval date of a cited result", source
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
        return finding(number, "value", "failed", "a value in a block that cites no result")
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
    for name, candidate in holdings.values:
        if allowed(number, candidate.kind) and fits(number, candidate):
            return finding(number, "value", "matched", candidate.where, name)
    if plain and digits in holdings.labels:
        return finding(
            number,
            "label number",
            "passed",
            "a number written as in a title or a label of a cited result",
            holdings.labels[digits],
        )
    return finding(number, "value", "failed", why_not(number, holdings))


def check(answer: str, language: Language, evidence: Sequence[Evidence]) -> Report:
    """Return the gate's report on an answer and the evidence of its run."""
    text = normalise(answer)
    known = {item.evidence_id: item for item in evidence}
    if len(known) != len(evidence):
        raise ValueError("two results of the run have the same evidence ID")
    numbers = read_numbers(text, language)
    # A marker in an HTML comment is not shown, so it cites nothing.
    hidden = [match.span() for match in re.finditer(r"<!--.*?(?:-->|$)", text, re.DOTALL)]
    holdings_of = {name: held(item) for name, item in known.items()}
    findings: list[Finding] = []
    for block in blocks(text):
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
    findings.sort(key=lambda item: (item.position, item.end))
    return Report(
        passed=all(item.outcome != "failed" for item in findings), findings=tuple(findings)
    )

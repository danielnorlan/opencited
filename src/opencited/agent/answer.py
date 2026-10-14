"""Answers and refusals, as `opencited ask` prints them and the evaluation suite reads them.

An answer holds its text with the citation markers, every result it cites with the
cells or steps whose values it uses, the gate report, the model and its settings, the
prompt version and the usage (spec 0004). A refusal holds its reason and the same
records, and the last gate report after a blocked answer.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Literal

from pydantic import Field

from opencited.agent.config import ModelConfig, Prices, Settings
from opencited.agent.run import Attempt, LimitName, RefusalReason, RunState, cost
from opencited.calc.result import CalculationResult
from opencited.gate import CalculationEvidence, Evidence, Report, SourceEvidence, cited_ids
from opencited.model import Frozen, TableId
from opencited.provenance import ProvenanceRecord
from opencited.sources import Language, QueryResult, TableMetadata
from opencited.values import ExactDecimal

# A citation marker, as the gate reads it; render.py numbers the sources with it.
MARKER = re.compile(r"\[((?:E|C)[1-9][0-9]*)\]")


class CellUse(Frozen):
    """A cell whose value the answer uses, with its codes and their labels."""

    codes: tuple[str, ...]
    labels: tuple[str, ...]
    value: ExactDecimal


class Citation(Frozen):
    """A source result that the answer cites. Only a query result has a provenance record."""

    evidence_id: str
    kind: Literal["query", "metadata", "search"]
    table_id: TableId | None
    title: str | None
    url: str | None
    provenance: ProvenanceRecord | None
    cells: tuple[CellUse, ...]


class CalculationCitation(Frozen):
    """A calculation that the answer cites, with every step and its SQL."""

    evidence_id: str
    result: CalculationResult
    steps: tuple[str, ...]


class ModelRecord(Frozen):
    """The endpoint and the model, as configured and as the endpoint named it."""

    base_url: str
    model: str
    reported: tuple[str, ...]
    settings: Settings
    prices: Prices | None


class RunUsage(Frozen):
    """What a run used. The cost is in the currency of the prices."""

    model_calls: int
    tool_calls: int
    input_tokens: int
    output_tokens: int
    seconds: ExactDecimal
    cost: ExactDecimal | None


class Answer(Frozen):
    kind: Literal["answer"] = "answer"
    question: str
    language: Language
    text: str
    citations: tuple[Citation, ...]
    calculations: tuple[CalculationCitation, ...]
    report: Report
    attempts: tuple[Attempt, ...] = Field(description="What became of each checked reply.")
    model: ModelRecord
    prompt_version: str
    usage: RunUsage


class Refusal(Frozen):
    kind: Literal["refusal"] = "refusal"
    question: str
    language: Language
    reason: RefusalReason
    detail: str
    limit: LimitName | None
    explanation: str | None
    report: Report | None
    attempts: tuple[Attempt, ...] = Field(description="What became of each checked reply.")
    model: ModelRecord
    prompt_version: str
    usage: RunUsage


def usage_of(state: RunState, prices: Prices | None) -> RunUsage:
    return RunUsage(
        model_calls=state.model_calls,
        tool_calls=state.tool_calls,
        input_tokens=state.input_tokens,
        output_tokens=state.output_tokens,
        seconds=state.seconds,
        cost=cost(state, prices),
    )


def model_record(config: ModelConfig, reported: Sequence[str]) -> ModelRecord:
    return ModelRecord(
        base_url=config.base_url,
        model=config.model,
        reported=tuple(dict.fromkeys(reported)),
        settings=config.settings,
        prices=config.prices,
    )


def cells_used(result: QueryResult, evidence_id: str, report: Report) -> tuple[CellUse, ...]:
    """Return every cell of a result that a matched number of the answer fits.

    The gate checks which values a number may be, so a number that fits several cells,
    such as one rounded to millions, lists them all.
    """
    labels = [{value.code: value.label for value in dim.values} for dim in result.dimensions]
    values = {cell.codes: cell.value for cell in result.observations}
    used: dict[tuple[str, ...], CellUse] = {}
    for finding in report.findings:
        for match in finding.matches:
            codes = match.cell
            if match.evidence != evidence_id or codes is None or codes in used:
                continue
            value = values.get(codes)
            if value is not None:
                names = tuple(
                    table.get(code, code) for table, code in zip(labels, codes, strict=True)
                )
                used[codes] = CellUse(codes=codes, labels=names, value=value)
    return tuple(used.values())


def citation_of(item: SourceEvidence, report: Report) -> Citation:
    result = item.result
    if isinstance(result, QueryResult):
        return Citation(
            evidence_id=item.evidence_id,
            kind="query",
            table_id=result.table_id,
            title=result.title,
            url=result.url,
            provenance=result.provenance,
            cells=cells_used(result, item.evidence_id, report),
        )
    if isinstance(result, TableMetadata):
        return Citation(
            evidence_id=item.evidence_id,
            kind="metadata",
            table_id=result.table_id,
            title=result.title,
            url=result.url,
            provenance=None,
            cells=(),
        )
    return Citation(
        evidence_id=item.evidence_id,
        kind="search",
        table_id=None,
        title=None,
        url=None,
        provenance=None,
        cells=(),
    )


def steps_used(item: CalculationEvidence, report: Report) -> tuple[str, ...]:
    """Return every step of a calculation that a matched number of the answer fits."""
    steps = [
        match.step
        for finding in report.findings
        for match in finding.matches
        if match.evidence == item.evidence_id and match.step is not None
    ]
    return tuple(dict.fromkeys(steps))


def answer_of(
    *,
    question: str,
    language: Language,
    text: str,
    report: Report,
    attempts: Sequence[Attempt],
    evidence: Sequence[Evidence],
    model: ModelRecord,
    prompt_version: str,
    usage: RunUsage,
) -> Answer:
    """Return a released answer with every result that its markers cite."""
    known = {item.evidence_id: item for item in evidence}
    citations: list[Citation] = []
    calculations: list[CalculationCitation] = []
    for evidence_id in cited_ids(text, language):
        item = known.get(evidence_id)
        if isinstance(item, SourceEvidence):
            citations.append(citation_of(item, report))
        elif isinstance(item, CalculationEvidence):
            calculations.append(
                CalculationCitation(
                    evidence_id=evidence_id, result=item.result, steps=steps_used(item, report)
                )
            )
    return Answer(
        question=question,
        language=language,
        text=text,
        citations=tuple(citations),
        calculations=tuple(calculations),
        report=report,
        attempts=tuple(attempts),
        model=model,
        prompt_version=prompt_version,
        usage=usage,
    )


__all__ = [
    "Answer",
    "CalculationCitation",
    "CellUse",
    "Citation",
    "ModelRecord",
    "Refusal",
    "RunUsage",
    "answer_of",
    "model_record",
    "usage_of",
]

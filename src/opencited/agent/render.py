"""An answer or a refusal as `opencited ask` prints it, in the language of the answer.

The answer's citation markers become numbered sources, in the order they first
appear, and a list of the sources follows the text. The JSON form, `--json`, holds
every record; this text names each source and where to find it.
"""

from __future__ import annotations

from opencited.agent.answer import MARKER, Answer, CalculationCitation, Citation, Refusal, cited
from opencited.sources import Language

WORDS: dict[Language, dict[str, str]] = {
    "no": {
        "sources": "Kilder",
        "table": "tabell",
        "Table": "Tabell",
        "retrieved": "hentet",
        "search": "Søk i Statistikkbanken",
        "calculation": "Beregning fra",
        "steps": "steg",
        "details": "Stegene med SQL står i --json.",
        "unanswered": "Ikke besvart",
        "reasons": "Begrunnelse fra modellen",
    },
    "en": {
        "sources": "Sources",
        "table": "table",
        "Table": "Table",
        "retrieved": "retrieved",
        "search": "A search of StatBank",
        "calculation": "Calculation from",
        "steps": "step",
        "details": "The steps with their SQL are in --json.",
        "unanswered": "Not answered",
        "reasons": "The model's reason",
    },
}
REASONS: dict[Language, dict[str, str]] = {
    "no": {
        "no data": "Kildene har ingen data for spørsmålet.",
        "outside the sources": "Spørsmålet gjelder ikke statistikk som SSB publiserer.",
        "gate blocked": "Hvert svar hadde tall som ikke kunne spores til en kilde.",
        "limit": "Kjøringen stoppet ved {limit}.",
    },
    "en": {
        "no data": "The sources hold no data for the question.",
        "outside the sources": "The question is not about statistics that SSB publishes.",
        "gate blocked": "Every answer held numbers that could not be traced to a source.",
        "limit": "The run stopped at {limit}.",
    },
}
LIMITS: dict[Language, dict[str, str]] = {
    "no": {
        "time": "tidsgrensen",
        "tokens": "grensen for tokens",
        "cost": "kostnadsgrensen",
        "model calls": "grensen for modellkall",
        "tool calls": "grensen for verktøykall",
    },
    "en": {
        "time": "its time limit",
        "tokens": "its token limit",
        "cost": "its cost limit",
        "model calls": "its limit of model calls",
        "tool calls": "its limit of tool calls",
    },
}


def source_line(item: Citation | CalculationCitation, language: Language) -> str:
    words = WORDS[language]
    if isinstance(item, CalculationCitation):
        tables = sorted({use.provenance.table_id for use in item.result.snapshots})
        steps = ", ".join(item.steps)
        named = f", {words['steps']} {steps}" if steps else ""
        return (
            f"{words['calculation']} {words['table']} {', '.join(tables)}{named}. "
            f"{words['details']}"
        )
    if item.provenance is not None:
        record = item.provenance
        moment = record.retrieved_at.strftime("%Y-%m-%d %H:%M")
        return (
            f"{record.publisher}, {words['table']} {record.table_id}: {record.table_title}. "
            f"{record.table_url}, {words['retrieved']} {moment} UTC"
        )
    if item.kind == "metadata":
        return f"{words['Table']} {item.table_id}: {item.title}. {item.url}"
    return words["search"]


def answer_text(answer: Answer) -> str:
    language = answer.language
    known: dict[str, Citation | CalculationCitation] = {
        item.evidence_id: item for item in (*answer.citations, *answer.calculations)
    }
    order = [evidence_id for evidence_id in cited(answer.text) if evidence_id in known]
    numbers = {evidence_id: number for number, evidence_id in enumerate(order, start=1)}
    body = MARKER.sub(
        lambda match: f"[{numbers[match[1]]}]" if match[1] in numbers else match[0],
        answer.text,
    )
    lines = [body.strip()]
    if numbers:
        lines += ["", f"{WORDS[language]['sources']}:"]
        lines += [
            f"[{number}] {source_line(known[evidence_id], language)}"
            for evidence_id, number in numbers.items()
        ]
    return "\n".join(lines)


def refusal_text(refusal: Refusal) -> str:
    language = refusal.language
    reason = REASONS[language][refusal.reason]
    if refusal.limit is not None:
        reason = reason.format(limit=LIMITS[language][refusal.limit])
    lines = [f"{WORDS[language]['unanswered']}: {reason}"]
    if refusal.explanation:
        lines.append(f"{WORDS[language]['reasons']}: {refusal.explanation}")
    return "\n".join(lines)


def text_of(result: Answer | Refusal) -> str:
    """Return an answer with numbered sources, or a refusal with its reason."""
    return answer_text(result) if isinstance(result, Answer) else refusal_text(result)


__all__ = ["text_of"]

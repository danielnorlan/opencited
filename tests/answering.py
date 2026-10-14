"""Evidence and answers for the agent's tests, made from recorded responses.

The evidence is a query of table 05810, its metadata and a search, all replayed from
`tests/fixtures/ssb/`, and a calculation over the query's snapshot. Every number in
an answer here is formatted from that evidence by the project's formatter.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from decimal import Decimal

from recorded import FIXTURES
from snapshotting import population

from opencited.agent.answer import Answer, model_record, usage_of
from opencited.agent.answer import answer_of as assemble
from opencited.agent.config import ModelConfig, Prices, Settings
from opencited.agent.run import RunState
from opencited.calc.engine import Engine
from opencited.calc.plan import Plan
from opencited.calc.result import CalculationResult
from opencited.gate import CalculationEvidence, Evidence, Report, SourceEvidence, check
from opencited.http import make_client
from opencited.http.replay import replay_transport
from opencited.snapshots import SnapshotStore
from opencited.sources import Language, QueryResult, SearchResult
from opencited.sources.ssb import BASE_URL, SsbClient
from opencited.values import format_number

CONFIG = ModelConfig(
    base_url="https://models.example.org/v1",
    model="a-model",
    key_variable="A_KEY",
    settings=Settings(temperature=Decimal("0.2"), seed=7),
    prices=Prices(input=Decimal("0.28"), output=Decimal("0.42")),
)
TOTAL = {"Kjonn": "0", "Alder": "999B", "ContentsCode": "Personer"}


@dataclass(frozen=True)
class Evidences:
    query: QueryResult
    calculation: CalculationResult
    evidence: tuple[Evidence, ...]

    def value(self, year: str) -> Decimal:
        codes = ("0", "999B", "Personer", year)
        found = next(cell.value for cell in self.query.observations if cell.codes == codes)
        assert found is not None
        return found

    def step(self, name: str) -> Decimal:
        value = self.calculation.step(name).value
        assert value is not None
        return value


def search() -> SearchResult:
    async def work() -> SearchResult:
        async with make_client(BASE_URL, transport=replay_transport(FIXTURES)) as http:
            return await SsbClient(http, revision_period=False).search("befolkning", "no", 10)

    return asyncio.run(work())


def evidences(store: SnapshotStore) -> Evidences:
    """Return E1 (a query), E2 (its metadata), E3 (a search) and C1 (a calculation)."""
    retrieval = population()
    snapshot_id = store.write(retrieval).name
    steps = [
        {
            "name": "before",
            "operation": "value",
            "snapshot": snapshot_id,
            "codes": TOTAL | {"Tid": "2025"},
        },
        {
            "name": "after",
            "operation": "value",
            "snapshot": snapshot_id,
            "codes": TOTAL | {"Tid": "2026"},
        },
        {
            "name": "growth",
            "operation": "change_percent",
            "old": "before",
            "new": "after",
            "decimals": 2,
        },
    ]
    calculation = Engine(store).run(Plan.model_validate_json(json.dumps({"steps": steps})))
    evidence: tuple[Evidence, ...] = (
        SourceEvidence("E1", retrieval.result),
        SourceEvidence("E2", retrieval.metadata),
        SourceEvidence("E3", search()),
        CalculationEvidence("C1", calculation),
    )
    return Evidences(retrieval.result, calculation, evidence)


def answer_text(found: Evidences, language: Language) -> str:
    """Return an answer that cites C1, E1 and E2, with numbers formatted from them."""
    after, growth = (
        format_number(found.value("2026"), language),
        format_number(found.step("growth"), language),
    )
    if language == "no":
        return (
            f"Folketallet økte med {growth} prosent fra 2025 til 2026 [C1] [E1].\n\n"
            f"I 2026 bodde det {after} personer i Norge [E1], ifølge tabell 05810 [E2]."
        )
    return (
        f"The population grew by {growth} per cent from 2025 to 2026 [C1] [E1].\n\n"
        f"In 2026, {after} people lived in Norway [E1], according to table 05810 [E2]."
    )


def answer(store: SnapshotStore, language: Language = "no") -> tuple[Answer, Evidences, Report]:
    found = evidences(store)
    text = answer_text(found, language)
    report = check(text, language, found.evidence)
    assert report.passed, report.text()
    state = RunState(
        model_calls=3,
        tool_calls=4,
        input_tokens=12_000,
        output_tokens=900,
        seconds=Decimal("41.250"),
    )
    result = assemble(
        question="Hvor mange bor i Norge?"
        if language == "no"
        else "How many people live in Norway?",
        language=language,
        text=text,
        report=report,
        evidence=found.evidence,
        model=model_record(CONFIG, ["a-model-2026-10-01", "a-model-2026-10-01"]),
        prompt_version="1",
        usage=usage_of(state, CONFIG.prices),
    )
    return result, found, report

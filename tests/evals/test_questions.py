"""Question sets and reference runs, against the recordings in `tests/fixtures/ssb/`.

The questions here read selections that are already recorded. Their expected numbers
come from the engine's run of each plan; a test compares them with the engine's result
over the same snapshots, never with a number written by hand.
"""

from __future__ import annotations

import asyncio
import json
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest
from recorded import FIXTURES

from opencited.evals.questions import Question, QuestionError, load_questions
from opencited.evals.reference import Reference, ReferencePlanError, reference
from opencited.evals.score import Expectation, ExpectedNumber
from opencited.http import make_client
from opencited.http.replay import replay_transport
from opencited.snapshots import SnapshotStore
from opencited.sources.ssb import BASE_URL, SsbClient

TOTAL = {"Kjonn": "0", "Alder": "999B", "ContentsCode": "Personer"}
TWO_YEARS = {
    "Kjonn": ["0"],
    "Alder": ["999B"],
    "ContentsCode": ["Personer"],
    "Tid": ["2025", "2026"],
}
EVERY_AGE = {"Kjonn": ["*"], "Alder": ["*"], "ContentsCode": ["Personer"], "Tid": ["top(3)"]}


def lookup(language: str = "en") -> dict[str, Any]:
    return {
        "id": f"lookup-population-{language}",
        "language": language,
        "text": "How many people lived in Norway in 2026?",
        "category": "lookup",
        "expected": {
            "outcome": "answer",
            "queries": [
                {"name": "Q1", "table_id": "05810", "selection": TWO_YEARS, "language": language}
            ],
            "plan": {
                "steps": [
                    {
                        "name": "after",
                        "operation": "value",
                        "snapshot": "Q1",
                        "codes": TOTAL | {"Tid": "2026"},
                    }
                ]
            },
            "numbers": [{"step": "after", "decimals": 0}],
        },
    }


def change() -> dict[str, Any]:
    return {
        "id": "change-population-no",
        "language": "no",
        "text": "Hvor mye økte folketallet fra 2025 til 2026, i prosent?",
        "category": "change",
        "expected": {
            "outcome": "answer",
            "queries": [
                {"name": "Q1", "table_id": "05810", "selection": TWO_YEARS, "language": "no"}
            ],
            "plan": {
                "steps": [
                    {
                        "name": "before",
                        "operation": "value",
                        "snapshot": "Q1",
                        "codes": TOTAL | {"Tid": "2025"},
                    },
                    {
                        "name": "after",
                        "operation": "value",
                        "snapshot": "Q1",
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
            },
            "numbers": [{"step": "growth", "decimals": 1}],
        },
    }


def ranking() -> dict[str, Any]:
    return {
        "id": "ranking-age-en",
        "language": "en",
        "text": "Which age group had the most people in 2026?",
        "category": "ranking",
        "expected": {
            "outcome": "answer",
            "queries": [
                {"name": "Q1", "table_id": "05810", "selection": EVERY_AGE, "language": "en"}
            ],
            "plan": {
                "steps": [
                    {
                        "name": "largest",
                        "operation": "rank",
                        "snapshot": "Q1",
                        "selection": {
                            "Kjonn": ["0"],
                            "Alder": ["00-06", "07-15", "16-44", "45-66", "67-79", "80+"],
                            "ContentsCode": ["Personer"],
                            "Tid": ["2026"],
                        },
                        "by": "Alder",
                        "order": "highest",
                        "count": 1,
                    }
                ]
            },
            "numbers": [{"step": "largest", "decimals": 0, "position": 1}],
            "label": "largest",
        },
    }


def refusal() -> dict[str, Any]:
    return {
        "id": "out-of-scope-weather",
        "language": "no",
        "text": "Hvor varmt blir det i Oslo i morgen?",
        "category": "out of scope",
        "expected": {"outcome": "refusal"},
    }


def written(tmp_path: Path, *questions: dict[str, Any], name: str = "v1.jsonl") -> Path:
    path = tmp_path / name
    path.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in questions))
    return path


def run_reference(question: Question, store: SnapshotStore) -> Reference:
    async def work() -> Reference:
        async with make_client(BASE_URL, transport=replay_transport(FIXTURES)) as http:
            return await reference(question, SsbClient(http, revision_period=False), store)

    return asyncio.run(work())


def test_a_question_set_is_read_with_its_version(tmp_path: Path) -> None:
    path = written(tmp_path, lookup(), change(), ranking(), refusal())

    questions = load_questions(path)

    assert questions.version == "v1"
    assert [item.id for item in questions.questions] == [
        "lookup-population-en",
        "change-population-no",
        "ranking-age-en",
        "out-of-scope-weather",
    ]


def broken(question: dict[str, Any], *path: str | int, value: object) -> dict[str, Any]:
    """Return a copy of a question with one field set to ``value``, or removed for None."""
    copy: dict[str, Any] = json.loads(json.dumps(question))
    target: Any = copy
    for key in path[:-1]:
        target = target[key]
    if value is None:
        del target[path[-1]]
    else:
        target[path[-1]] = value
    return copy


@pytest.mark.parametrize(
    ("question", "message"),
    [
        (broken(lookup(), "category", value="unanswerable"), "expects the other outcome"),
        (broken(refusal(), "category", value="lookup"), "expects the other outcome"),
        (broken(lookup(), "expected", "numbers", value=[]), "at least 1 item"),
        (
            broken(lookup(), "expected", "numbers", value=[{"step": "before", "decimals": 0}]),
            "no step 'before'",
        ),
        (broken(lookup(), "expected", "queries", 0, "name", value="Q2"), "must read every query"),
        (broken(ranking(), "expected", "label", value=None), "a ranking question expects a label"),
        (broken(ranking(), "expected", "numbers", 0, "position", value=None), "names a position"),
        (broken(lookup(), "expected", "numbers", 0, "position", value=1), "names a position"),
        (
            broken(ranking(), "expected", "queries", 0, "language", value="no"),
            "language of the question",
        ),
        (broken(lookup(), "expected", "plan", "steps", 0, "operation", value="avg"), "operation"),
        (broken(lookup(), "id", value="Lookup 1"), "String should match pattern"),
    ],
)
def test_a_question_that_breaks_the_schema_names_its_line(
    tmp_path: Path, question: dict[str, Any], message: str
) -> None:
    path = written(tmp_path, refusal(), question)

    with pytest.raises(QuestionError, match=r"v1\.jsonl, line 2: ") as caught:
        load_questions(path)

    assert message in str(caught.value)


def test_a_repeated_id_or_key_and_a_wrong_name_are_refused(tmp_path: Path) -> None:
    with pytest.raises(QuestionError, match="line 2: the ID out-of-scope-weather is repeated"):
        load_questions(written(tmp_path, refusal(), refusal()))
    path = tmp_path / "v2.jsonl"
    path.write_text('{"id": "a", "id": "b"}\n')
    with pytest.raises(QuestionError, match="names id more than once"):
        load_questions(path)
    with pytest.raises(QuestionError, match="named by its version"):
        load_questions(written(tmp_path, refusal(), name="questions.jsonl"))


def test_the_reference_numbers_come_from_the_plan_run_on_the_recorded_data(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path)
    question = Question.model_validate_json(json.dumps(change()))

    result = run_reference(question, store)

    assert result.result is not None
    growth = result.result.step("growth")
    assert result.expectation == Expectation(
        question_id="change-population-no",
        language="no",
        category="change",
        outcome="answer",
        numbers=(
            ExpectedNumber(step="growth", exact=str(growth.exact), kind="per cent", decimals=1),
        ),
    )
    [(name, snapshot_id)] = result.snapshots
    assert name == "Q1"
    assert store.read(snapshot_id).manifest.snapshot_id == snapshot_id


def test_a_ranking_expects_the_first_cell_s_value_and_label(tmp_path: Path) -> None:
    question = Question.model_validate_json(json.dumps(ranking()))

    result = run_reference(question, SnapshotStore(tmp_path))

    assert result.result is not None
    [first] = [cell for cell in result.result.step("largest").ranked if cell.position == 1]
    assert result.expectation.label == first.labels[1]
    assert result.expectation.numbers == (
        ExpectedNumber(step="largest", exact=str(Fraction(first.value)), kind="amount", decimals=0),
    )


def test_a_question_that_should_be_refused_reads_nothing(tmp_path: Path) -> None:
    result = run_reference(
        Question.model_validate_json(json.dumps(refusal())), SnapshotStore(tmp_path)
    )

    assert result.expectation.outcome == "refusal"
    assert (result.snapshots, result.result) == ((), None)


def test_a_ranking_place_without_a_cell_is_an_error(tmp_path: Path) -> None:
    question = broken(
        ranking(), "expected", "plan", "steps", 0, "selection", "Alder", value=["999B"]
    )
    tied = broken(question, "expected", "plan", "steps", 0, "selection", "Kjonn", value=["0"])
    # A ranking of one cell has no second place.
    absent = broken(tied, "expected", "numbers", 0, "position", value=2)
    absent = broken(absent, "expected", "label", value=None)
    absent = broken(absent, "category", value="lookup")

    with pytest.raises(ReferencePlanError, match="0 cells at place 2"):
        run_reference(Question.model_validate_json(json.dumps(absent)), SnapshotStore(tmp_path))

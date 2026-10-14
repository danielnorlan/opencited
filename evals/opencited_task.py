"""The evaluation suite as an Inspect task (spec 0005, ADR 0012).

Run it with the model under evaluation set by Inspect's model option, for example::

    uv run --group evals inspect eval evals/opencited_task.py --model PROVIDER/MODEL \\
        -T price_input=0.28 -T price_output=0.42 -T max_cost=0.05

Each question runs its reference plan on the day's data, then the agent answers it
inside Inspect's agent bridge, which sends the agent's model calls to the model under
evaluation, and the scorer of ``opencited.evals`` gives the verdict. The bridge gives
the agent no tool besides its own four: web search, code execution and the client's
own MCP servers are turned off, and the agent's model settings are forwarded.

Inspect's limits on tokens, time and cost are set above the agent's, so the agent
stops first and refuses with a reason. A question in a period that ADR 0007 keeps
runs away from is not run, and its score says so. ``evals/report.py`` writes the
run report from the log.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from inspect_ai import Task, task
from inspect_ai.agent import agent_bridge
from inspect_ai.dataset import MemoryDataset, Sample
from inspect_ai.model import ModelOutput
from inspect_ai.scorer import CORRECT, INCORRECT, NOANSWER, Score, Scorer, Target, accuracy, scorer
from inspect_ai.solver import Generate, Solver, TaskState, solver
from pydantic import TypeAdapter

from opencited.agent.answer import Answer
from opencited.agent.config import Limits, ModelConfig, Prices, Settings
from opencited.agent.model import ModelClient
from opencited.evals.questions import Question, QuestionSet, load_questions
from opencited.evals.run import Outcome, QuestionResult, Shared, Skipped, run_question
from opencited.evals.score import Failure
from opencited.http import make_client
from opencited.http.cache import ResponseCache
from opencited.http.limiter import RateLimiter
from opencited.snapshots import SnapshotStore
from opencited.sources.ssb import BASE_URL, SsbClient

ROOT = Path(__file__).resolve().parent
QUESTIONS = ROOT / "questions" / "v1.jsonl"
RUNS = ROOT / "runs"
# The bridge answers requests for the model "inspect" without sending them anywhere,
# so the base URL is never reached and the key is a placeholder, not a secret.
BRIDGE_KEY = "OPENCITED_BRIDGE_KEY"
BRIDGE_ENVIRONMENT = {BRIDGE_KEY: "answered-by-the-agent-bridge"}
OUTCOME = "opencited_outcome"
# Inspect's limits stay this far above the agent's, so the agent stops first. The
# time also covers the reference plan's fetches, which wait on the rate limit.
TOKEN_MARGIN = Decimal("1.5")
TIME_MARGIN = 3
COST_MARGIN = Decimal("1.5")

OUTCOMES: TypeAdapter[Outcome] = TypeAdapter(Outcome)


def kept(outcome: Outcome) -> object:
    """Return an outcome as the sample's store keeps it: JSON-compatible values."""
    return OUTCOMES.dump_python(outcome, mode="json")


def outcome_of(stored: object) -> Outcome:
    """Read an outcome back from a sample's store; the strict models read it as JSON."""
    return OUTCOMES.validate_json(json.dumps(stored))


def bridge_config(
    *, temperature: str | None, seed: int | None, prices: Prices | None, limits: Limits
) -> ModelConfig:
    """Return the agent's configuration for a model reached through the agent bridge."""
    return ModelConfig(
        base_url="http://inspect.invalid/v1",
        model="inspect",
        key_variable=BRIDGE_KEY,
        settings=Settings(temperature=Decimal(temperature) if temperature else None, seed=seed),
        prices=prices,
        limits=limits,
    )


def samples(questions: QuestionSet) -> list[Sample]:
    """Return one sample a question, with its ID, text, language and category."""
    return [
        Sample(
            id=question.id,
            input=question.text,
            metadata={
                "question_set": questions.version,
                "language": question.language,
                "category": question.category,
                "expected": question.expected.outcome,
            },
        )
        for question in questions.questions
    ]


def shown_text(outcome: Outcome) -> str:
    if isinstance(outcome, Skipped):
        return ""
    result = outcome.result
    if isinstance(result, Answer):
        return result.text
    if isinstance(result, Failure):
        return f"The run failed: {result.message}"
    return f"Refused: {result.reason}"


@solver
def answer_questions(questions: QuestionSet, config: ModelConfig, run_dir: Path) -> Solver:
    """Run each sample's question with the agent behind the bridge, and keep the outcome."""
    by_id: dict[str, Question] = {question.id: question for question in questions.questions}
    run_dir.mkdir(parents=True, exist_ok=True)
    shared = Shared(cache=ResponseCache(run_dir / "responses.sqlite"), limiter=RateLimiter())
    store = SnapshotStore(run_dir / "snapshots")
    store.check()

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        question = by_id[str(state.sample_id)]
        async with (
            make_client(BASE_URL) as http,
            agent_bridge(
                web_search=False,
                code_execution=False,
                client_mcp_servers=False,
                forward_generation_config=True,
            ),
            ModelClient(config, environ=BRIDGE_ENVIRONMENT) as model,
        ):
            source = SsbClient(http, cache=shared.cache, limiter=shared.limiter)
            outcome = await run_question(
                question, model=model, source=source, shared=shared, store=store
            )
        state.store.set(OUTCOME, kept(outcome))
        state.output = ModelOutput.from_content(model="opencited", content=shown_text(outcome))
        return state

    return solve


def score_of(outcome: Outcome) -> Score:
    """Return Inspect's score for an outcome, with the verdict as its metadata."""
    if isinstance(outcome, Skipped):
        return Score(value=NOANSWER, explanation="not run: a period that ADR 0007 avoids")
    verdict = outcome.verdict
    return Score(
        value=CORRECT if verdict.correct else INCORRECT,
        answer=shown_text(outcome),
        explanation=verdict.reason,
        metadata={"verdict": verdict.model_dump(mode="json")},
    )


@scorer(metrics=[accuracy()])
def verdict() -> Scorer:
    """Score a sample by the verdict that ``opencited.evals.score`` gave it."""

    async def score_sample(state: TaskState, target: Target) -> Score:
        return score_of(outcome_of(state.store.get(OUTCOME)))

    return score_sample


@task
def opencited(
    questions: str = str(QUESTIONS),
    run_dir: str = str(RUNS),
    temperature: str | None = None,
    seed: int | None = None,
    price_input: str | None = None,
    price_output: str | None = None,
    max_cost: str | None = None,
) -> Task:
    """The first question set, answered by the agent and scored without a model."""
    question_set = load_questions(Path(questions))
    prices = (
        Prices(input=Decimal(price_input), output=Decimal(price_output))
        if price_input and price_output
        else None
    )
    limits = Limits(cost=Decimal(max_cost) if max_cost else None)
    config = bridge_config(temperature=temperature, seed=seed, prices=prices, limits=limits)
    return Task(
        dataset=MemoryDataset(samples(question_set)),
        solver=answer_questions(question_set, config, Path(run_dir)),
        scorer=verdict(),
        token_limit=int(limits.tokens * TOKEN_MARGIN),
        time_limit=limits.seconds * TIME_MARGIN,
        # Inspect takes the cost as a float; it is a limit, not a value from a source.
        cost_limit=float(limits.cost * COST_MARGIN) if limits.cost is not None else None,
        metadata={"question_set": question_set.version},
    )


__all__ = ["QuestionResult", "opencited", "score_of", "verdict"]

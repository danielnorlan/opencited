"""Write the report of an evaluation run from its Inspect log (spec 0005).

Usage: uv run --group evals python evals/report.py LOG [--out DIR]

The report holds the question set version, the commit and whether the tree was
clean, the model as Inspect named it and the endpoint given for the run, the model
names that the endpoint gave in its replies, Inspect's generation settings, the
agent's model settings and prices, the date, the snapshot IDs and the result of every
question, the counts of the scorer's report, and the questions the run did not run.

The script refuses a log of a run that did not finish, one with more than one epoch,
and one with samples outside the question set; a question without a sample is listed
as not run. It writes only an allow list of generation settings, so headers and
request bodies stay out, refuses an endpoint URL with a user name, password or query,
and refuses to write a report that holds the value of an environment variable whose
name says it is a key, a token, a secret or a password. Published reports are
committed under ``evals/results/``.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from inspect_ai.event import ModelEvent
from inspect_ai.log import EvalLog, EvalSample, read_eval_log
from opencited_task import OUTCOME, QUESTIONS, outcome_of

from opencited.agent.config import ModelConfig
from opencited.evals.questions import load_questions
from opencited.evals.run import Outcome, RunReport, Skipped, run_report

RESULTS = Path(__file__).resolve().parent / "results"
# Generation settings that change results and hold no credential.
GENERATION = (
    "temperature",
    "top_p",
    "top_k",
    "seed",
    "max_tokens",
    "frequency_penalty",
    "presence_penalty",
    "stop_seqs",
    "effort",
    "reasoning_effort",
    "reasoning_tokens",
    "reasoning_mode",
    "verbosity",
)
SECRET_NAME = re.compile(r"KEY|TOKEN|SECRET|PASSWORD", re.IGNORECASE)
# Shorter values would match ordinary text by chance.
MIN_SECRET = 8


class ReportError(ValueError):
    """A log that cannot give a complete report, or a report that would hold a secret."""


def outcome_of_sample(sample: EvalSample) -> Outcome:
    kept = sample.store.get(OUTCOME)
    if kept is not None:
        return outcome_of(kept)
    if sample.limit is not None:
        detail = f"Inspect's {sample.limit.type} limit"
    elif sample.error is not None:
        detail = sample.error.message
    else:
        detail = "no outcome kept"
    return Skipped(question_id=str(sample.id), reason="stopped", detail=detail)


def reported_models(log: EvalLog) -> tuple[str, ...]:
    """Return the model names in the replies, as the model events recorded them."""
    names = [
        event.output.model
        for sample in log.samples or ()
        for event in sample.events
        if isinstance(event, ModelEvent) and event.output.model
    ]
    return tuple(dict.fromkeys(names))


def endpoint_of(url: str | None) -> str | None:
    if url is None:
        return None
    # The same rules as the agent's own base URL: no user name, password or query.
    ModelConfig(base_url=url, model="check", key_variable="CHECK")
    return url


def generation_of(settings: Mapping[str, Any]) -> dict[str, Any]:
    return {name: settings[name] for name in GENERATION if settings.get(name) is not None}


def question_set_of(log: EvalLog) -> str:
    version = (log.eval.metadata or {}).get("question_set")
    if not isinstance(version, str):
        raise ReportError("the log names no question set")
    return version


def outcomes_of(log: EvalLog) -> tuple[Outcome, ...]:
    """Return one outcome a question of the set, in the set's order."""
    samples = log.samples or []
    if any(sample.epoch != 1 for sample in samples):
        raise ReportError("the log has more than one epoch; a report covers one")
    by_id = {str(sample.id): sample for sample in samples}
    version = question_set_of(log)
    questions = load_questions(QUESTIONS.with_name(f"{version}.jsonl")).questions
    unknown = sorted(set(by_id) - {question.id for question in questions})
    if unknown:
        raise ReportError(f"the log has samples outside the set {version}: {', '.join(unknown)}")
    return tuple(
        outcome_of_sample(by_id[question.id])
        if question.id in by_id
        else Skipped(question_id=question.id, reason="stopped", detail="no sample in the log")
        for question in questions
    )


def report_of_log(log: EvalLog) -> RunReport:
    if log.status != "success":
        raise ReportError(f"the run did not finish: its log has the status {log.status}")
    spec = log.eval
    revision = spec.revision
    if revision is None:
        raise ReportError("the log names no commit; run the suite from a clone of the repository")
    try:
        endpoint = endpoint_of(spec.model_base_url)
    except ValueError as error:
        raise ReportError(f"the endpoint cannot go in a report: {error}") from None
    return run_report(
        question_set=question_set_of(log),
        commit=f"{revision.commit}{' (dirty)' if revision.dirty else ''}",
        date=datetime.fromisoformat(spec.created),
        model=spec.model,
        endpoint=endpoint,
        generation=generation_of(spec.model_generate_config.model_dump(mode="json")),
        outcomes=outcomes_of(log),
        reported=reported_models(log),
    )


def check_secrets(text: str, environ: Mapping[str, str]) -> None:
    for name, value in environ.items():
        if SECRET_NAME.search(name) and len(value) >= MIN_SECRET and value in text:
            raise ReportError(f"the report holds the value of {name}; it is not written")


def file_name(report: RunReport) -> str:
    model = re.sub(r"[^A-Za-z0-9.-]+", "-", report.model).strip("-")
    return f"{report.date:%Y-%m-%dT%H%M}-{report.question_set}-{model}.json"


def main(arguments: list[str] | None = None, environ: Mapping[str, str] = os.environ) -> int:
    parser = argparse.ArgumentParser(description="Write the report of an evaluation run.")
    parser.add_argument("log", type=Path, help="the Inspect log of the run")
    parser.add_argument("--out", type=Path, default=RESULTS, help="directory of the report")
    options = parser.parse_args(sys.argv[1:] if arguments is None else arguments)
    try:
        report = report_of_log(read_eval_log(str(options.log)))
        text = report.model_dump_json(indent=2) + "\n"
        check_secrets(text, environ)
    except ReportError as error:
        print(f"report: {error}", file=sys.stderr)
        return 1
    out: Path = options.out
    out.mkdir(parents=True, exist_ok=True)
    path = out / file_name(report)
    if path.exists():
        print(f"report: {path} exists; a published report is not replaced", file=sys.stderr)
        return 1
    path.write_text(text, encoding="utf-8")
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Write the report of an evaluation run from its Inspect log (spec 0005).

Usage: uv run --group evals python evals/report.py LOG [--out DIR]

The report holds the question set version, the commit, the model and the endpoint as
Inspect named them, Inspect's generation settings, the agent's model settings and
prices, the date, the snapshot IDs
and the result of every question, the counts of the scorer's report, and the
questions the run did not run. It holds no API key: the agent reached the model only
through Inspect's agent bridge, with a placeholder key. Published reports are
committed under ``evals/results/``.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

from inspect_ai.log import EvalLog, read_eval_log
from opencited_task import OUTCOME, outcome_of

from opencited.evals.run import Outcome, RunReport, run_report

RESULTS = Path(__file__).resolve().parent / "results"


def outcomes_of(log: EvalLog) -> tuple[Outcome, ...]:
    """Return the outcome that the solver kept for each sample, in the log's order."""
    found: list[Outcome] = []
    for sample in log.samples or ():
        kept = sample.store.get(OUTCOME)
        if kept is None:
            raise ValueError(f"sample {sample.id} holds no outcome; the solver did not finish")
        found.append(outcome_of(kept))
    return tuple(found)


def report_of_log(log: EvalLog) -> RunReport:
    spec = log.eval
    revision = spec.revision
    if revision is None:
        raise ValueError("the log names no commit; run the suite from a clone of the repository")
    commit = f"{revision.commit}{' (dirty)' if revision.dirty else ''}"
    return run_report(
        question_set=str(spec.metadata.get("question_set", "")) if spec.metadata else "",
        commit=commit,
        date=datetime.fromisoformat(spec.created),
        model=spec.model,
        endpoint=spec.model_base_url or spec.model,
        generation=spec.model_generate_config.model_dump(mode="json", exclude_none=True),
        outcomes=outcomes_of(log),
    )


def file_name(report: RunReport) -> str:
    model = re.sub(r"[^A-Za-z0-9.-]+", "-", report.model).strip("-")
    return f"{report.date:%Y-%m-%d}-{report.question_set}-{model}.json"


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write the report of an evaluation run.")
    parser.add_argument("log", type=Path, help="the Inspect log of the run")
    parser.add_argument("--out", type=Path, default=RESULTS, help="directory of the report")
    options = parser.parse_args(sys.argv[1:] if arguments is None else arguments)
    report = report_of_log(read_eval_log(str(options.log)))
    out: Path = options.out
    out.mkdir(parents=True, exist_ok=True)
    path = out / file_name(report)
    path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# Evaluation suite

The suite measures how often the agent answers correctly, refuses when it should and
is blocked by the evidence gate ([spec 0005](../docs/specs/0005-evaluation-suite.md)).
It runs as an Inspect task, with Inspect pinned in the `evals` dependency group
([ADR 0012](../docs/adr/0012-inspect-evaluations.md)).

| Path | Contents |
| --- | --- |
| `questions/v1.jsonl` | The first question set, with a reference plan for each question that has an answer |
| `opencited_task.py` | The Inspect task: the reference plans, the agent behind Inspect's agent bridge, and the scorer |
| `report.py` | Writes the report of a run from its Inspect log |
| `tests/` | Tests of the task and the report, which need no model |
| `results/` | Reports of published runs |

## Running the suite

A run calls SSB's API and the model under evaluation, which Inspect's model option
sets. Prices per million input and output tokens and a cost limit per question keep
the run within its budget:

```sh
uv sync --group evals
uv run --group evals inspect eval evals/opencited_task.py --model PROVIDER/MODEL \
    -T price_input=0.28 -T price_output=0.42 -T max_cost=0.05
uv run --group evals python evals/report.py logs/LOG.eval
```

Inspect reads the key of the model provider from that provider's usual environment
variable; the agent itself holds only a placeholder key, since the bridge answers its
calls. `-T temperature=` and `-T seed=` set the agent's model settings, which the
bridge forwards. A run keeps its snapshots and SSB's responses under `evals/runs/`,
and Inspect writes its log under `logs/`; git ignores both.

A question is not run in the periods that
[ADR 0007](../docs/adr/0007-ssb-pxwebapi-v2.md) keeps scheduled jobs away from,
04:55 to 08:15 and 11:25 to 11:45 in Norwegian time, and the report lists it.

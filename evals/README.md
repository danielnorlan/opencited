# Evaluation suite

The suite measures how often the agent answers correctly, refuses when it should and
is blocked by the evidence gate ([spec 0005](../docs/specs/0005-evaluation-suite.md)).
It runs as an Inspect task, with Inspect pinned in the `evals` dependency group
([ADR 0012](../docs/adr/0012-inspect-evaluations.md)).

| Path | Contents |
| --- | --- |
| `questions/v1.jsonl` | The first question set, with a reference plan for each question that has an answer |
| `questions/v2.jsonl` | The second set: questions about prices, wages, the national accounts, crime, marriages, income and employment, and questions where a made-up number would be easy |
| `opencited_task.py` | The Inspect task: the reference plans, the agent behind Inspect's agent bridge, and the scorer |
| `report.py` | Writes the report of a run from its Inspect log |
| `tests/` | Tests of the task and the report, which need no model |
| `results/` | Reports of published runs |

## Running the suite

A run calls SSB's API and the model under evaluation, which Inspect's model option
sets. It needs the model's prices per million input and output tokens and a cost
limit per question, so that the limit times the number of questions stays within the
run's budget. Run one question at a time, since every question shares SSB's rate
limit and a wait for it counts against the agent's time:

```sh
uv sync --group evals
uv run --group evals inspect eval evals/opencited_task.py --model PROVIDER/MODEL \
    --max-samples 1 -T price_input=0.28 -T price_output=0.42 -T max_cost=0.05
uv run --group evals python evals/report.py logs/LOG.eval
```

The task runs set v1 unless `-T questions=evals/questions/v2.jsonl` names another.

## The second set

Set v2 has 43 questions, 23 in Norwegian and 20 in English. Its 31 answerable
questions read ten tables that set v1 does not read, among them the consumer price
index (14710, 14711), wages (13312), the national accounts (09189), reported offences
(08484), weddings and divorces (05713, 05692), household income (06946) and
employment (03778, 09788). Five of them read values in thousands of persons or in
millions of kroner. One asks for the marriages of 1900, which table 05803 holds, so a
refusal there is wrong. Each of its 12 questions that should be refused gives a
`reason`: a month or week that SSB has not published or does not publish, a
municipality that does not exist, a street, a person, a company, dogs, a share price,
a translation, code and a future election. The reasons were checked against the
tables' metadata and searches of the API on 2026-10-14.

Inspect reads the key of the model provider from that provider's usual environment
variable; the agent holds only a placeholder key, since the bridge answers its calls.
`-T temperature=` and `-T seed=` set the agent's model settings, which the bridge
forwards; Inspect's own `--temperature` and `--seed` take precedence over them, and the
report records both. Inspect's token and time limits lie above the agent's. Its cost
limit needs the model's prices in Inspect's own form, so pass `--model-cost-config`
with `--cost-limit` set above `max_cost` to add it.

Each run keeps its snapshots and SSB's responses in a folder of its own under
`evals/runs/`, and Inspect writes its log under `logs/`; git ignores both. The report
script refuses a log of a run that did not finish, and one that would put a key in
the report.

A question is not run in the periods that
[ADR 0007](../docs/adr/0007-ssb-pxwebapi-v2.md) keeps scheduled jobs away from,
04:55 to 08:15 and 11:25 to 11:45 in Norwegian time, nor when its reference plan
cannot run, and the report lists it. An error while the agent answers is scored as a
failure, so one question cannot end the run.

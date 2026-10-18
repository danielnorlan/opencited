# Roadmap

OpenCited is built in spare time, so the targets are quarters. The order of the
milestones matters more than the dates.

| Milestone | Scope | Target | Status |
| --- | --- | --- | --- |
| M0 Foundation | Repository, CI, development tooling, decision records and the first specification | Q4 2026 | Done |
| M1 MCP server | SSB source with table search, metadata and data with provenance, published on PyPI and in the MCP Registry | Q4 2026 | In progress |
| M2 Evidence gate | Calculation engine, evidence gate, agent and the first evaluation set | Q1 2027 | Done; the [latest published runs](../evals/results/README.md) answered 40 of 44 questions of set v1 and 40 of 43 of set v3 correctly |
| M3 Search and tracing | Hybrid table search, tracing and model routing | Q1 2027 | Planned |
| M4 Public demo | Demo site and a public results page | Q2 2027 | Planned |

The current work is in the [plan for M1 and M2](plans/2026-10-13-m1-m2.md).

## Later

- A benchmark of 500 or more questions, including unanswerable and adversarial
  ones.
- Alerts about new statistics, published after human approval.
- A small open model trained with reinforcement learning against verifiable
  answers, served with vLLM.
- More sources: other statistics offices that use PxWebApi, international
  statistics through SDMX, and health guidance after a risk review.

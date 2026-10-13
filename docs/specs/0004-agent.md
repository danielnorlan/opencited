# Spec 0004: Agent

| Field | Value |
| --- | --- |
| Status | Draft |
| Milestone | M2 |
| Owner | Daniel Norlan |
| Created | 2026-10-13 |

## Goal

The agent answers questions about Norwegian statistics in Norwegian or English.
Every number in a released answer passes the evidence gate and links to its source.
When the agent cannot back an answer, it refuses and says why.

## Background

- [ADR 0004](../adr/0004-evidence-gate.md): the model never states a number from its
  own knowledge, a blocked answer may be retried within a budget, and after that the
  agent refuses.
- [ADR 0010](../adr/0010-agent-loop.md): a loop of our own over a chat completions
  API, with the endpoint and the model as configuration.
- The tools come from [spec 0001](0001-ssb-mcp-server.md) and
  [spec 0002](0002-snapshots-and-calculation.md), and the gate from
  [spec 0003](0003-evidence-gate.md).

## Users

- People who ask questions with `opencited ask`.
- The evaluation suite ([spec 0005](0005-evaluation-suite.md)).

## Requirements

### Model

1. The agent calls a chat completions endpoint that is compatible with the OpenAI
   API, through the `openai` SDK and the project's `httpx2` client.
2. The base URL, the model name and the name of the environment variable that holds
   the API key are configuration. The key is read from the environment and never
   written to an answer, a log or a report.
3. Model settings that change results, such as the temperature and a seed where the
   endpoint takes one, are configuration and are recorded in every answer.
4. Prices per million input tokens and per million output tokens are optional
   configuration. When they are set, every answer reports its cost.

### Tools

1. The model gets the three tools of the MCP server, called through the official MCP
   client, and the `calculate` tool. It gets no other tools, and every tool only
   reads.
2. Every tool result gets an evidence ID: `E1`, `E2` and so on for source results,
   and `C1`, `C2` and so on for calculations. The model uses these IDs in calculation
   plans and in citation markers.
3. Values in tool results reach the model both as decimal strings and formatted for
   the language of the answer.
4. Text from tool results, such as titles and notes, reaches the model as data inside
   tool messages. The agent only runs tool calls that match a published tool schema.

### Loop

1. The agent sends a fixed system prompt, the question and the tool schemas. The
   prompt has a version, which every answer records.
2. It runs each tool call and returns the result, until the model replies without a
   tool call.
3. The gate checks the reply. When the gate blocks it, the agent returns the gate
   report to the model and asks for a corrected answer, at most twice.
4. Each question has limits of 12 model calls, 20 tool calls, 100,000 tokens and 300
   seconds, and a cost limit when prices are set. All limits are configuration. When
   a limit is reached, the agent stops and refuses.
5. The decision after each step, to run a tool, check an answer, retry or stop, is a
   plain function of the run state. Tokens are counted from the usage that the
   endpoint reports.

### Answers and refusals

1. A released answer holds the text with its citation markers, the language, the
   citations with their provenance records and the cells used, the calculations with
   their steps and SQL, the gate report, the model and its settings, the prompt
   version and the usage: model calls, tool calls, tokens, time and cost.
2. A refusal holds a reason: the sources have no data for the question, the question
   is outside the sources, the gate blocked every attempt, or a limit was reached. It
   holds the same usage and, after a blocked answer, the last gate report.
3. `opencited ask "<question>"` prints the answer with numbered sources, and
   `--json` prints the full answer as JSON. The answer uses the language of the
   question unless `--language` sets it.

## Verification

1. Unit tests for the decision functions, the counting of limits, the evidence IDs,
   the conversion of tool schemas and the assembly of answers, with real values and no
   model.
2. Integration tests of the MCP client wrapper against the server in the same
   process, with recorded fixtures.
3. Runs with a model use a real endpoint only: the evaluation suite and tests marked
   `live`, which the default test run leaves out.

## Acceptance criteria

- [ ] With a configured endpoint, the agent answers or refuses every question in the
      first question set, and every released answer passes the gate again when the
      scorer checks it.
- [ ] Changing the endpoint, the model or the key variable needs no code change.
- [ ] A blocked answer is retried with the gate report, and the agent refuses with a
      reason when the retries or a limit run out.
- [ ] Every limit stops the run, shown by tests of the decision functions.
- [ ] The JSON answer holds every citation, every calculation, the gate report, the
      model settings, the prompt version and the usage.
- [ ] No API key appears in an answer, a log or a report.

## Out of scope

- Conversations with more than one question, and streaming.
- Model routing and tracing (M3).
- A web interface (M4).

## Tasks

1. Model client and configuration.
2. MCP client wrapper, evidence IDs, formatted values and the `calculate` tool.
3. The loop: decision functions, limits and retries.
4. Answers, refusals and `opencited ask`.

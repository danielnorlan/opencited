# 0010. Agent loop over a chat completions API

- Status: Accepted
- Date: 2026-10-13

## Context

The architecture page planned the agent on LangGraph. The facts below were checked on
2026-10-13 against PyPI, the projects' documentation and a test run.

- `langchain-mcp-adapters` 0.3.2, the package that connects LangGraph agents to MCP
  servers, declares `mcp>=1.24.0,<2.0.0`. It cannot be installed next to `mcp` 2.3.0,
  which the M1 server uses.
- LangGraph 1.2.15 requires `langchain-core`, which requires `httpx`. The MCP SDK uses
  `httpx2`, so the dependency tree would hold two HTTP stacks
  ([ADR 0008](0008-http-client.md)).
- OpenRouter, DeepSeek and Ollama document tool calling on chat completions endpoints
  that are compatible with the OpenAI API. Support differs from model to model.
- The `openai` Python SDK 3.29.0 depends on `httpx2` and accepts an `httpx2` client.
- Inspect AI ran a plain completion from that SDK inside an evaluation through its
  agent bridge ([ADR 0012](0012-inspect-evaluations.md)). Tool calls through the
  bridge are not tested yet.
- Added on 2026-10-14: the first published run sent the agent's tool calls through
  the bridge to DeepSeek's endpoint for all 44 questions of set v1, with thinking
  mode off ([report](../../evals/results/README.md)). With thinking on,
  `deepseek-flash` requires the reasoning of earlier replies in every later request,
  which the agent does not send back.

## Decision

1. The agent is a small loop of our own. It sends the conversation and the tool
   schemas to a chat completions endpoint through the `openai` SDK, runs tool calls
   through the official MCP client and the calculation engine, and checks the answer
   with the evidence gate.
2. The base URL of the endpoint, the model name and the name of the environment
   variable that holds the API key are configuration. No provider is written into the
   code.
3. The decision after each step, the retries and the budgets are plain functions of
   the run state, tested with real values.
4. The agent's behaviour with a model is tested against real models only, in the
   evaluation suite and in tests marked `live`.

## Alternatives considered

- LangGraph. Its MCP adapter does not support `mcp` 2.x, so MCP calls would need
  custom nodes anyway, and it adds the LangChain packages and a second HTTP stack.
- One SDK per model provider. More code paths for the same tool calling.
- LiteLLM as a gateway. It belongs to model routing in M3 and is reconsidered there.

## Consequences

- Source clients, the MCP client and the model client share one HTTP stack in the
  package's own dependencies.
- The loop, the parsing of tool calls, the retries and the budget accounting are
  project code with their own tests.
- Any endpoint with tool calling on this API shape should work. Each evaluation run
  records the endpoint and the model, since results depend on both.

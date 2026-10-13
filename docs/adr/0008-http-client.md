# 0008. HTTP client, recorded fixtures and the response cache

- Status: Proposed
- Date: 2026-10-13

## Context

The official MCP Python SDK, version 2.3.0, depends on
[`httpx2`](https://github.com/pydantic/httpx2), a fork of `httpx` maintained by the
Pydantic team with the same API under a new import name.
[`httpx`](https://pypi.org/project/httpx/) itself has had no stable release since
0.28.1 in December 2024. The two common mocking libraries, `respx` and
`pytest-httpx`, support only `httpx` today.

Spec 0001 requires contract tests against recorded responses, and a cache whose
entries keep the original retrieval time and response hash. Data requests can be
POST requests ([ADR 0007](0007-ssb-pxwebapi-v2.md)).

## Decision

1. Source clients use `httpx2`, the same HTTP stack as the MCP SDK, with explicit
   timeouts and a user agent that names the project and its version.
2. Tests replay recorded responses through `httpx2.MockTransport`. A small handler
   maps the method, the URL and the body of a request to a recorded file. A request
   without a recording fails the test.
3. `scripts/record_ssb_fixtures.py` records fixtures from the live API. Each file
   holds the request, the response status and headers and the raw body, and its
   name carries the recording date. Cookies and authorisation headers are never
   stored.
4. The cache is a small SQLite store of our own, keyed by the method, the URL and a
   hash of the body. An entry keeps the raw bytes, the retrieval time and the
   SHA-256, so a cached result repeats the original provenance.

## Alternatives considered

- `httpx` with `pytest-httpx`. A second HTTP stack next to the one the SDK brings,
  on a library without a recent release.
- `hishel` for caching. It supports `httpx2`, but it follows the HTTP caching rules
  of the server and caches GET and HEAD only by default, while the cache here must
  keep provenance fields and cover POST.

## Consequences

- One HTTP stack in the dependency tree.
- The replay handler is project code and needs its own tests.
- If `respx` or `pytest-httpx` gain `httpx2` support, the handler can be replaced
  without changing the fixtures.

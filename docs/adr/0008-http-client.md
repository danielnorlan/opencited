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
   without a recording fails the test. A test setting points the server at a
   directory of recorded fixtures, so the end-to-end test over stdio replays them
   inside the server process.
3. `scripts/record_ssb_fixtures.py` records fixtures from the live API. Each
   recording is a JSON file with the request, the response status and the headers
   from an allow list, and a file with the body after content decoding. Both names
   carry the UTC date of the recording. Cookies and authorisation headers are never
   stored.
4. SSB's responses name the statistician responsible for the table, with a phone
   number and an email address, in `extension.contact`. The recorder cuts that
   member out of the body, so every other character stays as SSB sent it, and
   stores the SHA-256 of the original body in the fixture file. It refuses a body
   when it cannot show that no contact field, email address or string from the
   contact remains. A recorded
   raw response, as spec 0001 uses the term, is the stored body, and a replayed
   response carries its hash. The personal data check runs on every fixture.
5. The cache is a small SQLite store of our own in the user's cache directory,
   keyed by the method, the URL and a hash of the body. An entry keeps the raw
   bytes, the retrieval time and the SHA-256, so a cached result repeats the
   original provenance. An entry is served until the next 08:15 Norwegian time
   after its retrieval, since SSB publishes new figures at 08:00.
6. The cache and the snapshot directory hold whole bodies, contact details
   included, and lie outside the repository by default.

## Alternatives considered

- `httpx` with `pytest-httpx`. A second HTTP stack next to the one the SDK brings,
  on a library without a recent release.
- `hishel` for caching. It supports `httpx2`, but it follows the HTTP caching rules
  of the server and caches GET and HEAD only by default, while the cache here must
  keep provenance fields and cover POST.

## Consequences

- One HTTP stack in the package's own dependencies. The `evals` group adds `httpx`
  through Inspect AI ([ADR 0012](0012-inspect-evaluations.md)).
- The replay handler is project code and needs its own tests.
- If `respx` or `pytest-httpx` gain `httpx2` support, the handler can be replaced
  without changing the fixtures.

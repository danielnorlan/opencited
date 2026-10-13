# 0001. Record architecture decisions

- Status: Accepted
- Date: 2026-10-13

## Context

OpenCited will be built over several years, by one maintainer with little time each
week. Decisions and the reasons behind them must live in the repository, where
anyone who works on the project or reviews it can read them later.

## Decision

Significant decisions are recorded as numbered files in `docs/adr/`, with the
sections Context, Decision, Alternatives considered and Consequences. A record is
proposed in a pull request, and only the maintainer accepts one. A replaced record
stays in place, marked as superseded, with a link to its replacement.

## Alternatives considered

- Decisions in issues and pull requests only. They are hard to find later and are
  not versioned with the code.
- A wiki. It sits outside the repository and outside the review flow.

## Consequences

- The reasons for a design stay next to the code, versioned with it.
- Each record costs some writing, so only decisions that are expensive to reverse
  get one.

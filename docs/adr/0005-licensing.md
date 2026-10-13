# 0005. Licensing and ownership

- Status: Accepted
- Date: 2026-10-13

## Context

OpenCited is a public project with possible commercial use later. The goals are
wide adoption, credit to the author and the option to offer a hosted service.

## Decision

- Code: Apache License 2.0.
- Evaluation datasets and published results: Creative Commons Attribution 4.0
  (CC BY 4.0), so anyone who reuses them must credit the project.
- Source data keeps its own licence and is attributed wherever it appears.
  Statistics Norway publishes its statistics under CC BY 4.0. Whether that covers
  data taken through its API is an open question in
  [spec 0001](../specs/0001-ssb-mcp-server.md).
- Pull requests from outside contributors are not accepted for now. All copyright
  stays with the maintainer, which keeps relicensing and a commercial offering
  possible.

## Alternatives considered

- MIT. Similar freedoms, without an explicit patent licence.
- GNU AGPL 3.0. Anyone who offers a modified version as a network service must
  publish the changes. Many companies and public agencies avoid it, which works
  against adoption.
- A proprietary licence. It removes the value of open code for a portfolio and the
  trust that open code gives an evidence tool.

## Consequences

- Anyone, public agencies included, may use and modify the code commercially
  without paying. The value for the author comes from reputation, expertise and
  services around the project.
- Outside improvements arrive as issues and ideas. Accepting code contributions
  later requires a contributor licence agreement.

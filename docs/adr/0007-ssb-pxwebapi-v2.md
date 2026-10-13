# 0007. StatBank through PxWebApi 2

- Status: Accepted
- Date: 2026-10-13

## Context

Spec 0001 left open which PxWebApi version SSB runs, which selection expressions it
supports, which rate limits apply and how missing values appear. The answers below
come from SSB's API guide, the PxWebApi user guide and requests to the live API on
2026-10-13.

- PxWebApi 2 is SSB's production API. SSB launched it in autumn 2025 and keeps
  version 1 for a transition period
  ([SSB](https://www.ssb.no/en/api/pxwebapi)). The base URL is
  `https://data.ssb.no/api/pxwebapi/v2`, and `/config` reported API version 2.3.2
  and software version 2.5.0.
- GET URLs are limited to about 2,100 characters. A longer URL returned an HTML
  error page with no JSON body. POST takes the same selection in the body and has
  no such limit ([user guide](https://pxtools.github.io/PxWebApi/documentation/user-guide/)).
- Selections support `*`, `?`, `top`, `bottom`, `range`, `from` and `to`. In a GET
  URL an expression that contains a comma must be wrapped in square brackets, or
  the comma splits it.
- `/config` allows 800,000 cells per request and publishes no call limit. The API
  guide states 30 calls per minute, and the responses carried a header with 40 per
  60 seconds.
- In JSON-stat 2 data a missing value is `null`, and a `status` object maps its
  index to SSB's symbol: `.` not applicable, `..` not available, `:` confidential.
  The explanation of a symbol arrives as free text in the `note` list of the data
  response.
- SSB's API guide states that the API uses CC BY 4.0
  ([SSB](https://www.ssb.no/en/api/pxwebapiv2)).
- Metadata is updated at 05:00 and 11:30, when tables are unavailable, and new
  figures are published at 08:00. Figures that are revised at 08:00 are shown as 0
  or as a dot from 05:00 to 08:00, and the guide asks users to avoid 07:55 to 08:15
  unless they need that period. The guide gives no time zone, and this record
  assumes Norwegian time (Europe/Oslo).

The fixtures recorded on 2026-10-13 in `tests/fixtures/ssb/` confirm the API and
software versions, the cell limit, the missing call limit in `/config`, the rate
limit headers, the symbol `..` with its note, the expressions `*` and `top`, and two
errors: a URL of about 3,300 characters got HTTP 404 with an HTML page, and a table
that does not exist got HTTP 404 with a JSON error. The URL limit of about 2,100
characters, POST without that limit, brackets around expressions with commas, the
other expressions, the symbols `.` and `:`, the 30 calls a minute and the update
times rest on SSB's documentation, which the recordings do not cover. A recording of
the same cells in Norwegian shows that the note for `..` starts the same way in both
languages.

The provenance record links each table to its page in StatBank. On 2026-10-14 SSB's
population pages in Norwegian and English linked tables as
`https://www.ssb.no/statbank/table/<id>` and `https://www.ssb.no/en/statbank/table/<id>`,
and a test marked `live` checks that such pages answer.

## Decision

1. The SSB client uses PxWebApi 2 at `https://data.ssb.no/api/pxwebapi/v2`, in
   Norwegian or English.
2. Data requests use GET while the full encoded URL stays under 1,800 characters,
   with expressions that contain commas wrapped in brackets. Longer requests use
   POST. A GET URL can be opened by a reader, so short requests stay readable in the
   provenance record.
3. The client sends at most 30 requests per minute, or fewer when the rate limit
   headers of a response state a lower limit, and backs off on HTTP 429. A tool
   call waits at most 10 seconds for a free slot and then returns an error that
   says the limit was reached.
4. The project keeps its own limit of 5,000 cells per `query_table` call, far below
   SSB's limit, so a single answer cannot pull a whole table by accident.
5. Symbols are kept as delivered. The client attaches the matching note to each
   symbol when the note starts with the symbol followed by ` = `, and keeps every
   note in the result.
6. Scheduled jobs, such as the nightly smoke test and fixture recording, avoid
   04:55 to 08:15 and 11:25 to 11:45 Norwegian time. These periods are derived from
   the guide: they start five minutes before a metadata update and cover the
   revision period, the publication at 08:00 and fifteen minutes after the update
   at 11:30.
7. From 05:00 to 08:15 Norwegian time, `query_table` returns an error that names
   SSB's revision period instead of data, and the client caches no data response
   retrieved in that period. Search and metadata are not blocked.

## Alternatives considered

- PxWebApi 1. It still works, but SSB recommends version 2 and keeps version 1
  only for a transition period.
- POST for every request. Simpler, but the provenance record would never contain
  a link that a reader can open.
- Reading rate limits from `/config`. It publishes none today.

## Consequences

- Spec 0001 is corrected in four places: the API version, the URL limit, the rate
  limit source and the licence question. A requirement for the revision period
  follows in a later change to the specification.
- The server returns no data for about three hours every morning.
- Table 06856 has cells with `..`. No response with `.` or `:` has been found yet,
  so unit tests cover the symbol table for all three symbols, and contract tests
  for `.` and `:` follow when a recording contains them.
- SSB runs PxWebApi 2.5.0 while 2.7.0 is released, so the client tolerates
  unknown fields and logs the versions that `/config` reports.

# Recorded SSB responses

These files are responses from the API of Statistics Norway (SSB), recorded with
`scripts/record_ssb_fixtures.py`. The contract tests in `tests/sources/` replay them
through `opencited.http.replay`, so those tests make no network calls.

Each recording has two files. `NAME.DATE.json` holds the request, the response
status, the response headers kept from an allow list and the SHA-256 of the original
and the stored body. `NAME.DATE.body` holds the body after content decoding, apart
from the contact details of SSB staff, which the recorder removes
([ADR 0008](../../../docs/adr/0008-http-client.md)). The date is the UTC date of the
recording.

To record again, run `uv run python scripts/record_ssb_fixtures.py`, with
`--replace` on a day that already has recordings. The script
refuses to run in the periods that
[ADR 0007](../../../docs/adr/0007-ssb-pxwebapi-v2.md) keeps scheduled jobs away
from.

The recordings named `question-...`, with the `metadata-...` recordings of their
tables, hold what the reference plans of the first question set read, so that every
plan runs in CI
([spec 0005](../../../docs/specs/0005-evaluation-suite.md)). They come from
`uv run python scripts/record_ssb_fixtures.py --questions evals/questions/v1.jsonl`,
which records the metadata and data of each query once and skips requests that a
recording here already answers.

Source: Statistics Norway. The data is licensed under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

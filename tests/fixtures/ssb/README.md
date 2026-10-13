# Recorded SSB responses

These files are responses from the API of Statistics Norway (SSB), recorded with
`scripts/record_ssb_fixtures.py`. The contract tests replay them, so the tests make
no network calls.

Each recording has two files. `NAME.DATE.json` holds the request, the response
status, the response headers kept from an allow list and the SHA-256 of the original
and the stored body. `NAME.DATE.body` holds the body as SSB sent it, apart from the
contact details of SSB staff, which the recorder removes
([ADR 0008](../../../docs/adr/0008-http-client.md)). The date is the day of the
recording.

To record again, run `uv run python scripts/record_ssb_fixtures.py`. The script
refuses to run in the periods that
[ADR 0007](../../../docs/adr/0007-ssb-pxwebapi-v2.md) keeps scheduled jobs away
from.

Source: Statistics Norway. The data is licensed under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

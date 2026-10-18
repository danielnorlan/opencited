#!/usr/bin/env python3
"""Record responses from Statistics Norway's API as test fixtures.

Usage: uv run python scripts/record_ssb_fixtures.py [--out DIR] [--only NAME ...] [--replace]
       uv run python scripts/record_ssb_fixtures.py --questions FILE [--out DIR]

Each recording becomes two files, named with the UTC date of the recording:
NAME.DATE.json holds the request, the response status, the kept headers and two
hashes, and NAME.DATE.body holds the response body after content decoding. The body
keeps every character apart from the contact details of SSB staff, which are cut out,
and the JSON file records the SHA-256 of the original and of the stored body
(ADR 0008). The recorder refuses a body when it cannot show that no contact details
or email addresses remain. Headers come from an allow list, so cookies never reach a
fixture. The script waits between requests to stay within SSB's rate limit, and it
refuses to run in the periods that ADR 0007 keeps scheduled jobs away from.

With ``--questions``, it records what the reference plans of a question set read
(spec 0005): the metadata and the data of every query, with the client's selection
normalisation, code joining and parameter order, so that the replay finds them; a test
compares each with the request the client builds. A request that a fixture in the
directory already answers with HTTP 200 is not sent again, unless ``--replace`` is
given.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from datetime import time as clock
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import cast
from zoneinfo import ZoneInfo

import httpx2

BASE_URL = "https://data.ssb.no/api/pxwebapi/v2"
OUT = Path("tests/fixtures/ssb")
OSLO = ZoneInfo("Europe/Oslo")
# ADR 0007, decision 6.
AVOIDED_PERIODS = ((clock(4, 55), clock(8, 15)), (clock(11, 25), clock(11, 45)))
# Two and a half seconds apart keeps the recorder under 30 requests a minute.
PAUSE_SECONDS = 2.5
KEPT_HEADERS = frozenset(
    {
        "content-language",
        "content-type",
        "etag",
        "last-modified",
        "retry-after",
        "x-ratelimit-limit",
        "x-ratelimit-policy",
        "x-ratelimit-remaining",
        "x-ratelimit-resource",
    }
)
CONTACT_PATH = ("extension", "contact")
# Strings in a contact entry that also appear elsewhere in every response and say
# nothing about a person.
ORGANISATIONS = frozenset({"statistics norway", "statistisk sentralbyrå"})
# Shorter strings from a contact entry would match ordinary words by chance.
MIN_PERSONAL_LENGTH = 4
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
BOM = b"\xef\xbb\xbf"
# Numbers stay text, so locating a member never turns a value into a float.
DECODER = json.JSONDecoder(parse_float=str, parse_int=str)
WHITESPACE = " \t\n\r"


class UnsafeBodyError(ValueError):
    """A response body that the recorder cannot show to be free of personal data."""


class RecordingError(RuntimeError):
    """A response that differs from what the recording expects."""


@dataclass(frozen=True)
class Recording:
    """One request to record, and why the fixtures need it."""

    name: str
    method: str
    path: str
    params: tuple[tuple[str, str], ...] = ()
    body: dict[str, object] | None = None
    reason: str = ""
    expected_status: int = 200


def data_params(language: str, selection: dict[str, str]) -> tuple[tuple[str, str], ...]:
    """Return the query of a GET data request: the language, the format and the codes."""
    codes = tuple((f"valueCodes[{dimension}]", value) for dimension, value in selection.items())
    return (("lang", language), *codes, ("outputFormat", "json-stat2"))


# One code repeated 450 times makes a URL of about 3,300 characters, beyond SSB's
# limit of about 2,100.
LONG_TID = ",".join(["2026"] * 450)

RECORDINGS: tuple[Recording, ...] = (
    Recording("config", "GET", "/config", reason="API version, languages and limits"),
    Recording(
        "search-befolkning-no",
        "GET",
        "/tables",
        (("query", "befolkning"), ("lang", "no"), ("pageSize", "10")),
        reason="table search in Norwegian",
    ),
    Recording(
        "search-population-en",
        "GET",
        "/tables",
        (("query", "population"), ("lang", "en"), ("pageSize", "10")),
        reason="table search in English",
    ),
    *(
        Recording(
            f"metadata-{table}-{language}",
            "GET",
            f"/tables/{table}/metadata",
            (("lang", language),),
            reason="dimensions, codes, labels, units and notes",
        )
        for table in ("06856", "06743", "05810")
        for language in ("no", "en")
    ),
    Recording(
        "data-06856-symbols-en",
        "GET",
        "/tables/06856/data",
        data_params(
            "en",
            {"RegionSamisk": "1849,1850,1853", "ContentsCode": "*", "Tid": "2008,2009,2010"},
        ),
        reason="cells with the symbol '..' and the note that explains it",
    ),
    Recording(
        "data-06856-symbols-no",
        "GET",
        "/tables/06856/data",
        data_params(
            "no",
            {"RegionSamisk": "1849,1850,1853", "ContentsCode": "*", "Tid": "2008,2009,2010"},
        ),
        reason="the same cells with the note in Norwegian",
    ),
    Recording(
        "data-06743-decimals-no",
        "GET",
        "/tables/06743/data",
        data_params(
            "no",
            {
                "FiskeslagSamiske": "80a",
                "SamiskeElver": "18,18000000127,18144000000",
                "ContentsCode": "Fangst,Gjennomsnitt",
                "Tid": "2015,2016",
            },
        ),
        reason="values with one decimal, and zeros",
    ),
    Recording(
        "data-06946-shared-codes-en",
        "GET",
        "/tables/06946/data",
        data_params(
            "en",
            {
                "Region": "01,02",
                "HusholdType": "01,02",
                "ContentsCode": "AntallHushold",
                "Tid": "2019",
            },
        ),
        reason="two dimensions that share codes and label them differently",
    ),
    Recording(
        "metadata-03024-no",
        "GET",
        "/tables/03024/metadata",
        (("lang", "no"),),
        reason="dimensions, codes and labels of a table by week",
    ),
    Recording(
        "data-03024-weeks-no",
        "GET",
        "/tables/03024/data",
        data_params("no", {"VareGrupper2": "01", "ContentsCode": "Vekt", "Tid": "2026U40,2026U41"}),
        reason="values for weeks, whose periods are codes such as 2026U41",
    ),
    *(
        Recording(
            f"data-05810-{language}",
            "GET",
            "/tables/05810/data",
            data_params(
                language,
                {"Kjonn": "0", "Alder": "999B", "ContentsCode": "Personer", "Tid": "2025,2026"},
            ),
            reason="the same cells in both languages",
        )
        for language in ("no", "en")
    ),
    Recording(
        "data-05810-expressions-en",
        "GET",
        "/tables/05810/data",
        data_params(
            "en", {"Kjonn": "*", "Alder": "*", "ContentsCode": "Personer", "Tid": "top(3)"}
        ),
        reason="selection expressions '*' and 'top'",
    ),
    Recording(
        "data-05810-offset-en",
        "GET",
        "/tables/05810/data",
        data_params(
            "en", {"Kjonn": "0", "Alder": "999B", "ContentsCode": "Personer", "Tid": "[top(2,1)]"}
        ),
        reason="top with an offset on the time dimension, in brackets since it has a comma",
    ),
    Recording(
        "data-05810-bottom-en",
        "GET",
        "/tables/05810/data",
        data_params(
            "en", {"Kjonn": "0", "Alder": "bottom(2)", "ContentsCode": "Personer", "Tid": "2026"}
        ),
        reason="bottom on a dimension that is not time",
    ),
    Recording(
        "data-05810-masks-en",
        "GET",
        "/tables/05810/data",
        data_params(
            "en", {"Kjonn": "0", "Alder": "0?-??", "ContentsCode": "Personer", "Tid": "202?"}
        ),
        reason="masks with '?'",
    ),
    Recording(
        "data-05810-range-en",
        "GET",
        "/tables/05810/data",
        data_params(
            "en",
            {
                "Kjonn": "0",
                "Alder": "999B",
                "ContentsCode": "Personer",
                "Tid": "[range(2020,2022)],from(2025)",
            },
        ),
        reason="range in brackets combined with from",
    ),
    Recording(
        "data-05810-eliminated-en",
        "GET",
        "/tables/05810/data",
        data_params("en", {"ContentsCode": "Personer", "Tid": "2026"}),
        reason="dimensions that can be eliminated, left out",
    ),
    Recording(
        "data-05810-order-en",
        "GET",
        "/tables/05810/data",
        data_params(
            "en", {"Kjonn": "1,2", "Alder": "999B", "ContentsCode": "Personer", "Tid": "2026"}
        ),
        reason="codes in another order than the metadata",
    ),
    Recording(
        "data-05810-post-en",
        "POST",
        "/tables/05810/data",
        (("lang", "en"), ("outputFormat", "json-stat2")),
        {
            "selection": [
                {"variableCode": "Kjonn", "valueCodes": ["1", "2"]},
                {"variableCode": "Alder", "valueCodes": ["999B"]},
                {"variableCode": "ContentsCode", "valueCodes": ["Personer"]},
                {"variableCode": "Tid", "valueCodes": ["2024", "2025", "2026"]},
            ]
        },
        reason="a data request sent as POST",
    ),
    Recording(
        "error-metadata-99999-en",
        "GET",
        "/tables/99999/metadata",
        (("lang", "en"),),
        reason="a table that does not exist",
        expected_status=404,
    ),
    Recording(
        "error-data-05810-long-url-en",
        "GET",
        "/tables/05810/data",
        data_params(
            "en", {"Kjonn": "0", "Alder": "999B", "ContentsCode": "Personer", "Tid": LONG_TID}
        ),
        reason="a GET URL longer than SSB accepts, answered with an HTML page",
        expected_status=404,
    ),
    Recording(
        "error-data-05810-missing-variable-en",
        "GET",
        "/tables/05810/data",
        data_params("en", {"Tid": "2026"}),
        reason="a selection without a dimension that the table requires",
        expected_status=400,
    ),
    Recording(
        "error-data-05810-unknown-value-en",
        "GET",
        "/tables/05810/data",
        data_params(
            "en", {"Kjonn": "0", "Alder": "999B", "ContentsCode": "Personer", "Tid": "1800"}
        ),
        reason="a code that the table does not have",
        expected_status=400,
    ),
    Recording(
        "error-data-05810-unknown-variable-en",
        "GET",
        "/tables/05810/data",
        data_params("en", {"Fylke": "03", "ContentsCode": "Personer", "Tid": "2026"}),
        reason="a dimension that the table does not have",
        expected_status=400,
    ),
    Recording(
        "error-data-99999-en",
        "GET",
        "/tables/99999/data",
        data_params("en", {"Tid": "2026"}),
        reason="data from a table that does not exist, answered with HTTP 400",
        expected_status=400,
    ),
)


def in_avoided_period(moment: datetime) -> bool:
    """Return whether a moment falls in a period that ADR 0007 keeps jobs away from."""
    local = moment.astimezone(OSLO).time()
    return any(start <= local < end for start, end in AVOIDED_PERIODS)


def skip_whitespace(text: str, index: int) -> int:
    """Return the first index at or after ``index`` that is not JSON whitespace."""
    while index < len(text) and text[index] in WHITESPACE:
        index += 1
    return index


def member_span(text: str, start: int, path: tuple[str, ...]) -> tuple[int, int, int] | None:
    """Return where the member at ``path`` starts, and where its value starts and ends.

    ``start`` is the index of the opening brace of the object to search. The member
    starts at its key, so cutting from there to the end of the value removes both.
    The text must be valid JSON without duplicate keys, which ``parsed`` checks.
    """
    if text[start] != "{":
        return None
    index = skip_whitespace(text, start + 1)
    while index < len(text) and text[index] != "}":
        key, after_key = DECODER.raw_decode(text, index)
        value_start = skip_whitespace(text, skip_whitespace(text, after_key) + 1)
        _, value_end = DECODER.raw_decode(text, value_start)
        if key == path[0]:
            if len(path) == 1:
                return index, value_start, value_end
            return member_span(text, value_start, path[1:])
        index = skip_whitespace(text, value_end)
        if index < len(text) and text[index] == ",":
            index = skip_whitespace(text, index + 1)
    return None


def cut_member(text: str, key_start: int, value_end: int) -> str:
    """Return the text without one member and the comma that joins it to the others."""
    after = skip_whitespace(text, value_end)
    if text[after] == ",":
        return text[:key_start] + text[skip_whitespace(text, after + 1) :]
    before = key_start - 1
    while text[before] in WHITESPACE:
        before -= 1
    if text[before] == ",":
        return text[:before] + text[value_end:]
    return text[:key_start] + text[value_end:]


def unique_members(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Build a JSON object, refusing duplicate keys, which would hide a second member."""
    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys):
        raise UnsafeBodyError("the body has duplicate keys")
    return dict(pairs)


def parsed(text: str) -> object:
    """Parse JSON with numbers kept as text and duplicate keys refused."""
    return json.loads(text, parse_float=str, parse_int=str, object_pairs_hook=unique_members)


def keys_and_strings(value: object) -> Iterator[str]:
    """Yield every key and every string inside a parsed JSON value."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in cast("list[object]", value):
            yield from keys_and_strings(item)
    elif isinstance(value, dict):
        for key, item in cast("dict[str, object]", value).items():
            yield key
            yield from keys_and_strings(item)


def contact_keys(contact: object) -> tuple[str, ...]:
    """Return the field names of a removed contact value, for the fixture's record."""
    entries = cast("list[object]", contact) if isinstance(contact, list) else [contact]
    names = {
        key
        for entry in entries
        if isinstance(entry, dict)
        for key in cast("dict[str, object]", entry)
    }
    return tuple(sorted(names))


def personal_strings(contact: object) -> set[str]:
    """Return the strings of a contact value that could identify a person, case-folded."""
    keys = set(contact_keys(contact))
    return {
        text.casefold()
        for text in keys_and_strings(contact)
        if text not in keys
        and text.casefold() not in ORGANISATIONS
        and len(text) >= MIN_PERSONAL_LENGTH
    }


def without_member(document: dict[str, object], path: tuple[str, ...]) -> dict[str, object]:
    """Return a parsed JSON object without the member at ``path``."""
    head, rest = path[0], path[1:]
    if not rest:
        return {key: value for key, value in document.items() if key != head}
    child = document.get(head)
    if not isinstance(child, dict):
        raise UnsafeBodyError(f"the body has no object at {head!r}")
    return {**document, head: without_member(cast("dict[str, object]", child), rest)}


def check_clean(document: object, personal: set[str]) -> None:
    """Refuse a parsed body that still holds a contact field, an email or a personal string."""
    for text in keys_and_strings(document):
        folded = text.casefold()
        if folded == "contact":
            raise UnsafeBodyError("the body has a contact field outside extension.contact")
        if EMAIL.search(text):
            raise UnsafeBodyError("the body holds an email address")
        if any(value in folded for value in personal):
            raise UnsafeBodyError("contact details still appear in the body")


def remove_contact(body: bytes, *, json_expected: bool = True) -> tuple[bytes, tuple[str, ...]]:
    """Return the body without ``extension.contact``, and the field names it removed.

    Every other character stays as it was, and a byte order mark is kept. The result
    is checked by parsing it: it must equal the original without the member, and no
    contact field, email address or string from the contact may remain. A body that
    is not JSON is kept as it is when ``json_expected`` is false and it holds no
    email address. Anything that cannot be checked raises ``UnsafeBodyError``.
    """
    prefix = BOM if body.startswith(BOM) else b""
    try:
        text = body[len(prefix) :].decode("utf-8")
    except UnicodeDecodeError as error:
        raise UnsafeBodyError("the body is not UTF-8") from error
    try:
        document = parsed(text)
    except (json.JSONDecodeError, RecursionError) as error:
        if json_expected:
            raise UnsafeBodyError("the body is not valid JSON") from error
        if EMAIL.search(text):
            raise UnsafeBodyError("the body holds an email address") from error
        return body, ()
    span = None
    if isinstance(document, dict):
        try:
            span = member_span(text, skip_whitespace(text, 0), CONTACT_PATH)
        except (ValueError, RecursionError) as error:
            raise UnsafeBodyError("the contact field could not be located") from error
    if span is None:
        check_clean(cast("object", document), set())
        return body, ()
    key_start, value_start, value_end = span
    contact = parsed(text[value_start:value_end])
    stored = cut_member(text, key_start, value_end)
    expected = without_member(cast("dict[str, object]", document), CONTACT_PATH)
    try:
        unchanged = parsed(stored) == expected
    except json.JSONDecodeError as error:
        raise UnsafeBodyError("removing the contact details changed other content") from error
    if not unchanged:
        raise UnsafeBodyError("removing the contact details changed other content")
    check_clean(expected, personal_strings(contact))
    return prefix + stored.encode("utf-8"), contact_keys(contact)


def sha256(data: bytes) -> str:
    """Return the hexadecimal SHA-256 of ``data``."""
    return hashlib.sha256(data).hexdigest()


def kept_headers(headers: httpx2.Headers) -> dict[str, str]:
    """Return the response headers that a fixture may keep, with lower-case names."""
    return {
        name.lower(): value
        for name, value in sorted(headers.items())
        if name.lower() in KEPT_HEADERS
    }


def build(client: httpx2.Client, recording: Recording) -> httpx2.Request:
    """Return the request for a recording, built the way the client will build it."""
    return client.build_request(
        recording.method, recording.path, params=list(recording.params), json=recording.body
    )


def envelope(
    recording: Recording,
    request: httpx2.Request,
    response: httpx2.Response,
    stored: bytes,
    removed_keys: tuple[str, ...],
    recorded_at: datetime,
    body_file: str,
) -> dict[str, object]:
    """Return the JSON document that describes one recording."""
    return {
        "name": recording.name,
        "reason": recording.reason,
        "recorded_at": recorded_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "request": {
            "method": request.method,
            "url": str(request.url),
            "body": request.content.decode("utf-8") if request.content else None,
        },
        "response": {
            "status": response.status_code,
            "headers": kept_headers(response.headers),
            "body_file": body_file,
            "body_sha256": sha256(stored),
            "original_body_sha256": sha256(response.content),
            "removed": ["extension.contact"] if removed_keys else [],
            "removed_fields": list(removed_keys),
        },
    }


def record(client: httpx2.Client, recording: Recording, out: Path, *, replace: bool) -> Path:
    """Send one request and write its two fixture files. Return the JSON file.

    Nothing is written unless the response has the expected status and its body
    passes ``remove_contact``. The body file is written before the JSON file that
    describes it.
    """
    recorded_at = datetime.now(UTC)
    stem = f"{recording.name}.{recorded_at.date().isoformat()}"
    body_path, json_path = out / f"{stem}.body", out / f"{stem}.json"
    if not replace and (body_path.exists() or json_path.exists()):
        raise FileExistsError(f"{json_path.name} exists; use --replace to record it again")
    request = build(client, recording)
    response = client.send(request)
    if response.status_code != recording.expected_status:
        raise RecordingError(
            f"expected HTTP {recording.expected_status}, got {response.status_code}"
        )
    json_expected = "json" in response.headers.get("content-type", "")
    stored, removed_keys = remove_contact(response.content, json_expected=json_expected)
    document = envelope(
        recording, request, response, stored, removed_keys, recorded_at, body_path.name
    )
    body_path.write_bytes(stored)
    json_path.write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return json_path


def question_recordings(path: Path) -> tuple[Recording, ...]:
    """Return the requests that the queries of a question set send, each once."""
    # Imported here, so that the fixed recordings need nothing from the package.
    from opencited.evals.questions import AnswerExpected, load_questions
    from opencited.sources.pxweb import normalise
    from opencited.sources.ssb import MAX_GET_URL_LENGTH, url_codes

    found: dict[tuple[str, str, tuple[tuple[str, str], ...]], Recording] = {}
    for question in load_questions(path).questions:
        expected = question.expected
        if not isinstance(expected, AnswerExpected):
            continue
        for query in expected.queries:
            table, language = query.table_id, query.language
            metadata = Recording(
                f"metadata-{table}-{language}",
                "GET",
                f"/tables/{table}/metadata",
                (("lang", language),),
                reason="dimensions, codes, labels, units and notes for a reference plan",
            )
            codes = normalise(query.selection)
            data = Recording(
                f"question-{question.id}-{query.name.lower()}",
                "GET",
                f"/tables/{table}/data",
                (
                    ("lang", language),
                    *(
                        (f"valueCodes[{dimension}]", url_codes(items))
                        for dimension, items in codes.items()
                    ),
                    ("outputFormat", "json-stat2"),
                ),
                reason=f"query {query.name} of question {question.id} in {path.name}",
            )
            for recording in (metadata, data):
                with httpx2.Client(base_url=BASE_URL) as client:
                    if len(str(build(client, recording).url)) >= MAX_GET_URL_LENGTH:
                        raise RecordingError(f"{recording.name}: the URL is too long for GET")
                found.setdefault((recording.method, recording.path, recording.params), recording)
    return tuple(found.values())


def recorded_requests(out: Path) -> set[tuple[str, str]]:
    """Return the method and URL of every request that a fixture answers with HTTP 200."""
    requests: set[tuple[str, str]] = set()
    for path in out.glob("*.json"):
        document = cast("dict[str, object]", json.loads(path.read_text(encoding="utf-8")))
        request = cast("dict[str, object]", document["request"])
        response = cast("dict[str, object]", document["response"])
        if response["status"] == 200:
            requests.add((str(request["method"]), str(request["url"])))
    return requests


def user_agent() -> str:
    """Return the user agent, which names the project and its version."""
    try:
        release = version("opencited")
    except PackageNotFoundError:
        release = "unknown"
    return f"opencited/{release} fixture recorder (+https://github.com/danielnorlan/opencited)"


def parse_arguments(arguments: list[str]) -> argparse.Namespace:
    """Parse the command line."""
    parser = argparse.ArgumentParser(description="Record SSB API responses as fixtures.")
    parser.add_argument("--out", type=Path, default=OUT, help="directory for the fixtures")
    parser.add_argument("--only", nargs="+", metavar="NAME", help="record only these recordings")
    parser.add_argument(
        "--replace", action="store_true", help="replace recordings made on the same day"
    )
    parser.add_argument(
        "--questions",
        type=Path,
        metavar="FILE",
        help="record what the reference plans of a question set read, apart from fixtures that exist",
    )
    return parser.parse_args(arguments)


def main(arguments: list[str] | None = None) -> int:
    """Record the selected recordings. Return 0, 1 when a recording fails, or 2 for bad input."""
    options = parse_arguments(sys.argv[1:] if arguments is None else arguments)
    out: Path = options.out
    if options.questions is not None:
        try:
            available = question_recordings(options.questions)
        except (ValueError, RecordingError) as error:
            print(f"{options.questions}: {error}", file=sys.stderr)
            return 2
        done = (
            recorded_requests(out)
            if out.is_dir() and not options.replace
            else set[tuple[str, str]]()
        )
        with httpx2.Client(base_url=BASE_URL) as client:
            available = tuple(
                item
                for item in available
                if (item.method, str(build(client, item).url)) not in done
            )
    else:
        available = RECORDINGS
    unknown = set(options.only or ()) - {r.name for r in available}
    if unknown:
        print(f"Unknown recordings: {', '.join(sorted(unknown))}", file=sys.stderr)
        return 2
    if in_avoided_period(datetime.now(UTC)):
        print("SSB updates or publishes data now (ADR 0007). Try again later.", file=sys.stderr)
        return 1
    selected = [r for r in available if not options.only or r.name in options.only]
    out.mkdir(parents=True, exist_ok=True)
    with httpx2.Client(
        base_url=BASE_URL,
        headers={"User-Agent": user_agent()},
        timeout=30.0,
        follow_redirects=False,
    ) as client:
        for number, recording in enumerate(selected):
            if number:
                time.sleep(PAUSE_SECONDS)
            try:
                path = record(client, recording, out, replace=options.replace)
            except (UnsafeBodyError, RecordingError, FileExistsError, httpx2.HTTPError) as error:
                print(f"{recording.name}: {error}", file=sys.stderr)
                return 1
            print(f"recorded {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

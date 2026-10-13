#!/usr/bin/env python3
"""Record responses from Statistics Norway's API as test fixtures.

Usage: uv run python scripts/record_ssb_fixtures.py [--out DIR] [--only NAME ...]

Each recording becomes two files: NAME.DATE.json holds the request, the response
status, the kept headers and two hashes, and NAME.DATE.body holds the response body.
The body keeps every byte that SSB sent apart from the contact details of SSB staff,
which are cut out, and the JSON file records the SHA-256 of both the original and the
stored body (ADR 0008). Headers come from an allow list, so cookies never reach a
fixture. The script waits between requests to stay within SSB's rate limit, and it
refuses to run in the periods that ADR 0007 keeps scheduled jobs away from.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from datetime import time as clock
from importlib.metadata import version
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
# The fields of a contact entry that identify a person. The organisation, "Statistics
# Norway", appears elsewhere in every response.
PERSONAL_FIELDS = ("name", "phone", "mail", "raw")
DECODER = json.JSONDecoder()
WHITESPACE = " \t\n\r"


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


def parsed(text: str) -> object:
    """Parse JSON with numbers kept as text, so a comparison never goes through float."""
    return json.loads(text, parse_float=str, parse_int=str)


def personal_strings(contact: object) -> list[str]:
    """Return the values of the personal fields in a parsed list of contact entries."""
    if not isinstance(contact, list):
        return []
    found: list[str] = []
    for entry in cast("list[object]", contact):
        if isinstance(entry, dict):
            fields = cast("dict[str, object]", entry)
            found.extend(
                value
                for key in PERSONAL_FIELDS
                if isinstance(value := fields.get(key), str) and value
            )
    return found


def without_contact(document: dict[str, object]) -> dict[str, object]:
    """Return a parsed response without ``extension.contact``."""
    extension = document.get(CONTACT_PATH[0])
    if not isinstance(extension, dict):
        raise ValueError("the response has no extension object")
    members = cast("dict[str, object]", extension)
    trimmed = {key: value for key, value in members.items() if key != CONTACT_PATH[1]}
    return {**document, CONTACT_PATH[0]: trimmed}


def remove_contact(body: bytes) -> tuple[bytes, bool]:
    """Return the body without ``extension.contact``, and whether it held one.

    Every other byte stays as it was. The result is checked by parsing both versions:
    they must differ only by the removed member, and no string from the contact may
    remain anywhere in the body.
    """
    try:
        text = body.decode("utf-8")
        original = parsed(text)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return body, False
    if not isinstance(original, dict):
        return body, False
    span = member_span(text, skip_whitespace(text, 0), CONTACT_PATH)
    if span is None:
        return body, False
    key_start, value_start, value_end = span
    contact = parsed(text[value_start:value_end])
    stored = cut_member(text, key_start, value_end)
    if parsed(stored) != without_contact(cast("dict[str, object]", original)):
        raise ValueError("removing the contact details changed other content")
    if any(value in stored for value in personal_strings(contact)):
        raise ValueError("contact details still appear in the body")
    return stored.encode("utf-8"), True


def sha256(data: bytes) -> str:
    """Return the hexadecimal SHA-256 of ``data``."""
    return hashlib.sha256(data).hexdigest()


def kept_headers(headers: httpx2.Headers) -> dict[str, str]:
    """Return the response headers that a fixture may keep, with lower-case names."""
    return {name: value for name, value in sorted(headers.items()) if name.lower() in KEPT_HEADERS}


def envelope(
    recording: Recording,
    request: httpx2.Request,
    response: httpx2.Response,
    stored: bytes,
    removed: bool,
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
            "removed": ["extension.contact"] if removed else [],
        },
    }


def record(client: httpx2.Client, recording: Recording, out: Path) -> Path:
    """Send one request and write its two fixture files. Return the JSON file."""
    request = client.build_request(
        recording.method, recording.path, params=list(recording.params), json=recording.body
    )
    response = client.send(request)
    if response.status_code != recording.expected_status:
        raise RuntimeError(
            f"{recording.name}: expected HTTP {recording.expected_status}, got {response.status_code}"
        )
    recorded_at = datetime.now(UTC)
    stem = f"{recording.name}.{recorded_at.date().isoformat()}"
    stored, removed = remove_contact(response.content)
    (out / f"{stem}.body").write_bytes(stored)
    document = envelope(recording, request, response, stored, removed, recorded_at, f"{stem}.body")
    path = out / f"{stem}.json"
    path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def parse_arguments(arguments: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Record SSB API responses as fixtures.")
    parser.add_argument("--out", type=Path, default=OUT, help="directory for the fixtures")
    parser.add_argument("--only", nargs="+", metavar="NAME", help="record only these recordings")
    return parser.parse_args(arguments)


def main(arguments: list[str] | None = None) -> int:
    options = parse_arguments(sys.argv[1:] if arguments is None else arguments)
    unknown = set(options.only or ()) - {r.name for r in RECORDINGS}
    if unknown:
        print(f"Unknown recordings: {', '.join(sorted(unknown))}", file=sys.stderr)
        return 2
    if in_avoided_period(datetime.now(UTC)):
        print("SSB updates or publishes data now (ADR 0007). Try again later.", file=sys.stderr)
        return 1
    selected = [r for r in RECORDINGS if not options.only or r.name in options.only]
    out: Path = options.out
    out.mkdir(parents=True, exist_ok=True)
    agent = (
        f"opencited/{version('opencited')} fixture recorder"
        " (+https://github.com/danielnorlan/opencited)"
    )
    with httpx2.Client(
        base_url=BASE_URL, headers={"User-Agent": agent}, timeout=30.0, follow_redirects=False
    ) as client:
        for number, recording in enumerate(selected):
            if number:
                time.sleep(PAUSE_SECONDS)
            path = record(client, recording, out)
            print(f"recorded {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

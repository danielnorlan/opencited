"""Tests for the SSB fixture recorder and for the fixtures it recorded. None calls SSB.

The removal tests insert a contact entry with invented values into a recorded body,
so they can check that the recorder gives back the recorded bytes exactly.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
import record_ssb_fixtures as recorder

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "ssb"
ENVELOPES = sorted(FIXTURES.glob("*.json"))
NAME = re.compile(r"^(?P<name>[a-z0-9-]+)\.(?P<date>\d{4}-\d{2}-\d{2})\.json$")
CONTACT = (
    '[{"name":"Kari Nordmann","organization":"Statistics Norway","phone":"00 00 00 00",'
    '"mail":"kari@example.com","raw":"Kari Nordmann, Statistics Norway# 00 00 00 00#kari@example.com"}]'
)


def load(path: Path) -> dict[str, object]:
    return cast("dict[str, object]", json.loads(path.read_text(encoding="utf-8")))


def response_of(path: Path) -> dict[str, object]:
    return cast("dict[str, object]", load(path)["response"])


def body_of(path: Path) -> bytes:
    return (FIXTURES / str(response_of(path)["body_file"])).read_bytes()


def metadata_body() -> str:
    return (
        FIXTURES / str(response_of(ENVELOPES_BY_NAME["metadata-05810-en"])["body_file"])
    ).read_text(encoding="utf-8")


ENVELOPES_BY_NAME = {str(load(path)["name"]): path for path in ENVELOPES}


def test_every_recording_has_a_fixture() -> None:
    assert {recording.name for recording in recorder.RECORDINGS} <= set(ENVELOPES_BY_NAME)


def test_every_body_file_has_a_description() -> None:
    described = {str(response_of(path)["body_file"]) for path in ENVELOPES}
    assert {path.name for path in FIXTURES.glob("*.body")} == described


@pytest.mark.parametrize("path", ENVELOPES, ids=lambda path: path.stem)
def test_stored_body_matches_its_hash(path: Path) -> None:
    assert hashlib.sha256(body_of(path)).hexdigest() == response_of(path)["body_sha256"]


@pytest.mark.parametrize("path", ENVELOPES, ids=lambda path: path.stem)
def test_fixture_keeps_only_allowed_headers(path: Path) -> None:
    headers = cast("dict[str, str]", response_of(path)["headers"])
    assert set(headers) <= recorder.KEPT_HEADERS
    assert "set-cookie" not in headers


@pytest.mark.parametrize("path", ENVELOPES, ids=lambda path: path.stem)
def test_file_name_carries_the_recording_date(path: Path) -> None:
    match = NAME.match(path.name)
    assert match is not None
    document = load(path)
    assert match["name"] == document["name"]
    assert str(document["recorded_at"]).startswith(match["date"])


@pytest.mark.parametrize("path", ENVELOPES, ids=lambda path: path.stem)
def test_no_stored_body_holds_contact_details(path: Path) -> None:
    body = body_of(path)
    assert recorder.remove_contact(body) == (body, False)


def test_data_and_metadata_had_contact_details_removed() -> None:
    removed = {name for name, path in ENVELOPES_BY_NAME.items() if response_of(path)["removed"]}
    assert {
        name for name in ENVELOPES_BY_NAME if name.startswith(("data-", "metadata-"))
    } == removed


def top_level_extension(text: str) -> tuple[int, int]:
    """Return where the top-level ``extension`` object of a recorded body opens and closes."""
    span = recorder.member_span(text, 0, ("extension",))
    assert span is not None
    _, value_start, value_end = span
    return value_start, value_end - 1


def test_removal_gives_back_the_recorded_bytes_when_contact_comes_first() -> None:
    recorded = metadata_body()
    opening, _ = top_level_extension(recorded)
    with_contact = f'{recorded[: opening + 1]}"contact":{CONTACT},{recorded[opening + 1 :]}'
    assert recorder.remove_contact(with_contact.encode()) == (recorded.encode(), True)


def test_removal_gives_back_the_recorded_bytes_when_contact_comes_last() -> None:
    recorded = metadata_body()
    _, closing = top_level_extension(recorded)
    with_contact = f'{recorded[:closing]},"contact":{CONTACT}{recorded[closing:]}'
    assert recorder.remove_contact(with_contact.encode()) == (recorded.encode(), True)


def test_removal_leaves_nested_extensions_alone() -> None:
    recorded = metadata_body()
    nested = recorded.index('"extension":{') + len('"extension":{')
    assert nested < top_level_extension(recorded)[0]
    with_contact = f'{recorded[:nested]}"contact":{CONTACT},{recorded[nested:]}'
    assert recorder.remove_contact(with_contact.encode()) == (with_contact.encode(), False)


def test_removal_handles_indented_json() -> None:
    document = {"label": "x", "extension": {"px": {"decimals": 0}, "contact": json.loads(CONTACT)}}
    stored, removed = recorder.remove_contact(json.dumps(document, indent=2).encode())
    assert removed
    assert json.loads(stored) == {"label": "x", "extension": {"px": {"decimals": 0}}}


def test_removal_refuses_when_a_personal_value_appears_elsewhere() -> None:
    document = {"note": ["Ask Kari Nordmann"], "extension": {"contact": json.loads(CONTACT)}}
    with pytest.raises(ValueError, match="still appear"):
        recorder.remove_contact(json.dumps(document).encode())


@pytest.mark.parametrize(
    "body",
    [b'{"apiVersion":"2.3.2"}', b"<!DOCTYPE html><html></html>", b"\xff\xfe", b"[1, 2]"],
)
def test_bodies_without_contact_details_stay_as_they_are(body: bytes) -> None:
    assert recorder.remove_contact(body) == (body, False)


@pytest.mark.parametrize(
    ("utc", "avoided"),
    [
        ((2, 54), False),
        ((2, 55), True),
        ((6, 14), True),
        ((6, 15), False),
        ((9, 25), True),
        ((9, 45), False),
        ((21, 0), False),
    ],
)
def test_avoided_periods_follow_norwegian_time(utc: tuple[int, int], avoided: bool) -> None:
    # 14 October 2026 is in summer time, two hours ahead of UTC.
    moment = datetime(2026, 10, 14, *utc, tzinfo=UTC)
    assert recorder.in_avoided_period(moment) is avoided


def test_data_params_put_the_codes_between_language_and_format() -> None:
    assert recorder.data_params("en", {"Tid": "2025,2026"}) == (
        ("lang", "en"),
        ("valueCodes[Tid]", "2025,2026"),
        ("outputFormat", "json-stat2"),
    )


def test_unknown_recording_names_are_rejected(capsys: pytest.CaptureFixture[str]) -> None:
    assert recorder.main(["--only", "no-such-recording"]) == 2
    assert "no-such-recording" in capsys.readouterr().err

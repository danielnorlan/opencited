"""Tests for the SSB fixture recorder and for the fixtures it recorded. None calls SSB.

The removal tests insert contact entries with invented values into recorded bodies, so
they can check that the recorder gives back the recorded bytes exactly, and that it
refuses bodies it cannot clean.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import httpx2
import pytest
import record_ssb_fixtures as recorder

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "ssb"
ENVELOPES = sorted(FIXTURES.glob("*.json"))
NAME = re.compile(r"^(?P<name>[a-z0-9-]+)\.(?P<date>\d{4}-\d{2}-\d{2})\.json$")
CONTACT = (
    '[{"name":"Kari Nordmann","organization":"Statistics Norway","phone":"00 00 00 00",'
    '"mail":"kari@example.com","raw":"Kari Nordmann, Statistics Norway# 00 00 00 00#kari@example.com"}]'
)
FIELDS = ("mail", "name", "organization", "phone", "raw")


def load(path: Path) -> dict[str, object]:
    return cast("dict[str, object]", json.loads(path.read_text(encoding="utf-8")))


def response_of(path: Path) -> dict[str, object]:
    return cast("dict[str, object]", load(path)["response"])


def body_of(path: Path) -> bytes:
    return (FIXTURES / str(response_of(path)["body_file"])).read_bytes()


def latest_by_name() -> dict[str, Path]:
    """Return the newest fixture of each recording, since file names sort by date."""
    latest: dict[str, Path] = {}
    for path in ENVELOPES:
        latest[str(load(path)["name"])] = path
    return latest


LATEST = latest_by_name()


def metadata_body() -> str:
    return body_of(LATEST["metadata-05810-en"]).decode("utf-8")


def extension_bounds(text: str) -> tuple[int, int]:
    """Return where the top-level ``extension`` object of a body opens and closes."""
    span = recorder.member_span(text, 0, ("extension",))
    assert span is not None
    _, value_start, value_end = span
    return value_start, value_end - 1


def with_member(text: str, member: str, *, at: str) -> str:
    """Return a body with ``member`` added first, last or in the middle of ``extension``."""
    opening, closing = extension_bounds(text)
    if at == "first":
        return f"{text[: opening + 1]}{member},{text[opening + 1 :]}"
    if at == "last":
        return f"{text[:closing]},{member}{text[closing:]}"
    first = recorder.member_span(text, opening, ("px",))
    assert first is not None
    return f"{text[: first[2]]},{member}{text[first[2] :]}"


# The recorded fixtures.


def test_every_recording_has_a_fixture() -> None:
    assert {recording.name for recording in recorder.RECORDINGS} <= set(LATEST)


@pytest.mark.parametrize("recording", recorder.RECORDINGS, ids=lambda recording: recording.name)
def test_fixture_request_matches_the_recording(recording: recorder.Recording) -> None:
    with httpx2.Client(base_url=recorder.BASE_URL) as client:
        request = recorder.build(client, recording)
    described = cast("dict[str, object]", load(LATEST[recording.name])["request"])
    assert described["method"] == request.method
    assert described["url"] == str(request.url)
    assert described["body"] == (request.content.decode() if request.content else None)
    assert response_of(LATEST[recording.name])["status"] == recording.expected_status


def test_every_body_file_has_a_description() -> None:
    described = {str(response_of(path)["body_file"]) for path in ENVELOPES}
    assert {path.name for path in FIXTURES.glob("*.body")} == described


@pytest.mark.parametrize("path", ENVELOPES, ids=lambda path: path.stem)
def test_stored_body_matches_its_hash(path: Path) -> None:
    response = response_of(path)
    digest = hashlib.sha256(body_of(path)).hexdigest()
    assert digest == response["body_sha256"]
    assert (digest == response["original_body_sha256"]) is (not response["removed"])


@pytest.mark.parametrize("path", ENVELOPES, ids=lambda path: path.stem)
def test_fixture_keeps_only_allowed_headers(path: Path) -> None:
    headers = cast("dict[str, str]", response_of(path)["headers"])
    assert set(headers) <= recorder.KEPT_HEADERS


@pytest.mark.parametrize("path", ENVELOPES, ids=lambda path: path.stem)
def test_file_name_carries_the_recording_date(path: Path) -> None:
    match = NAME.match(path.name)
    assert match is not None
    document = load(path)
    assert match["name"] == document["name"]
    assert str(document["recorded_at"]).startswith(match["date"])


@pytest.mark.parametrize("path", ENVELOPES, ids=lambda path: path.stem)
def test_no_stored_body_holds_contact_details(path: Path) -> None:
    content_type = cast("dict[str, str]", response_of(path)["headers"]).get("content-type", "")
    _, removed = recorder.remove_contact(body_of(path), json_expected="json" in content_type)
    assert removed == ()


def test_contact_details_were_removed_from_data_and_metadata() -> None:
    removed = {name for name, path in LATEST.items() if response_of(path)["removed"]}
    assert removed == {name for name in LATEST if name.startswith(("data-", "metadata-"))}
    for name in removed:
        assert response_of(LATEST[name])["removed_fields"] == list(FIELDS)


# Removing the contact member.


@pytest.mark.parametrize("at", ["first", "middle", "last"])
def test_removal_gives_back_the_recorded_bytes(at: str) -> None:
    recorded = metadata_body()
    body = with_member(recorded, f'"contact":{CONTACT}', at=at).encode()
    assert recorder.remove_contact(body) == (recorded.encode(), FIELDS)


@pytest.mark.parametrize("spacing", ["", " ", "\n  "])
def test_removal_of_the_only_member(spacing: str) -> None:
    body = f'{{"label":"x","extension":{{{spacing}"contact":{CONTACT}{spacing}}}}}'
    stored, removed = recorder.remove_contact(body.encode())
    assert removed == FIELDS
    assert stored.decode() == f'{{"label":"x","extension":{{{spacing}{spacing}}}}}'


def test_removal_keeps_the_indentation_of_indented_json() -> None:
    kept = {"label": "x", "extension": {"px": {"decimals": 0}}}
    document = {"label": "x", "extension": {"px": {"decimals": 0}, "contact": json.loads(CONTACT)}}
    stored, removed = recorder.remove_contact(json.dumps(document, indent=2).encode())
    assert removed == FIELDS
    assert stored.decode() == json.dumps(kept, indent=2)


def test_removal_of_several_entries_with_raw_unicode_and_escapes() -> None:
    contact = (
        '[{"name":"Åse \\"Bjørn\\" Ødegård","mail":"ase@example.com"},'
        '{"name":"Ola Nordmann","phone":"11 11 11 11"}]'
    )
    recorded = metadata_body()
    body = with_member(recorded, f'"contact":{contact}', at="first").encode()
    assert recorder.remove_contact(body) == (recorded.encode(), ("mail", "name", "phone"))


def test_removal_of_a_contact_object() -> None:
    recorded = metadata_body()
    member = '"contact":{"name":"Kari Nordmann","mail":"kari@example.com"}'
    body = with_member(recorded, member, at="last").encode()
    assert recorder.remove_contact(body) == (recorded.encode(), ("mail", "name"))


def test_removal_keeps_a_byte_order_mark() -> None:
    recorded = metadata_body()
    body = recorder.BOM + with_member(recorded, f'"contact":{CONTACT}', at="first").encode()
    assert recorder.remove_contact(body) == (recorder.BOM + recorded.encode(), FIELDS)


def test_numbers_are_located_as_text() -> None:
    huge = "9" * 5000
    body = f'{{"value":[{huge}],"extension":{{"contact":{CONTACT}}}}}'
    stored, removed = recorder.remove_contact(body.encode())
    assert removed == FIELDS
    assert stored.decode() == f'{{"value":[{huge}],"extension":{{}}}}'


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (b"\xff\xfe", "not UTF-8"),
        (b'{"value": [1, 2', "not valid JSON"),
        (b'{"extension":{"px":{}},"extension":{"contact":[]}}', "duplicate keys"),
        (b'{"extension":null,"extension":{"contact":[]}}', "duplicate keys"),
        (b'{"link":{"extension":{"contact":[]}}}', "contact field outside"),
        (b'{"note":["Write to post@example.com"]}', "email address"),
    ],
)
def test_removal_refuses_bodies_it_cannot_check(body: bytes, reason: str) -> None:
    with pytest.raises(recorder.UnsafeBodyError, match=reason):
        recorder.remove_contact(body)


@pytest.mark.parametrize(
    "note",
    ["Ask Kari Nordmann", "ASK KARI NORDMANN", 'Ask \\"Kari Nordmann\\"', "Call 00 00 00 00"],
)
def test_removal_refuses_when_a_personal_value_appears_elsewhere(note: str) -> None:
    body = f'{{"note":["{note}"],"extension":{{"contact":{CONTACT}}}}}'
    with pytest.raises(recorder.UnsafeBodyError, match="still appear"):
        recorder.remove_contact(body.encode())


def test_removal_refuses_when_other_content_would_change(monkeypatch: pytest.MonkeyPatch) -> None:
    def careless_cut(text: str, key_start: int, value_end: int) -> str:
        return text[:key_start] + text[value_end:]

    monkeypatch.setattr(recorder, "cut_member", careless_cut)
    body = f'{{"extension":{{"px":{{}},"contact":{CONTACT}}}}}'
    with pytest.raises(recorder.UnsafeBodyError, match="changed other content"):
        recorder.remove_contact(body.encode())


@pytest.mark.parametrize("body", [b'{"apiVersion":"2.3.2"}', b"[1, 2]", b'{"extension":{"px":{}}}'])
def test_json_without_contact_details_stays_as_it_is(body: bytes) -> None:
    assert recorder.remove_contact(body) == (body, ())


def test_a_page_that_is_not_json_stays_as_it_is_when_allowed() -> None:
    page = b"<!DOCTYPE html><html><title>404</title></html>"
    assert recorder.remove_contact(page, json_expected=False) == (page, ())
    with pytest.raises(recorder.UnsafeBodyError, match="email"):
        recorder.remove_contact(b"<p>post@example.com</p>", json_expected=False)


# Recording one response.


def answer(status: int, body: bytes) -> httpx2.MockTransport:
    def handler(request: httpx2.Request) -> httpx2.Response:
        headers = {
            "Content-Type": "application/json",
            "Set-Cookie": "X-Identity=1; Path=/",
            "X-RateLimit-Limit": "40",
        }
        return httpx2.Response(status, headers=headers, content=body, request=request)

    return httpx2.MockTransport(handler)


def test_record_writes_a_clean_fixture(tmp_path: Path) -> None:
    recorded = metadata_body()
    body = with_member(recorded, f'"contact":{CONTACT}', at="first").encode()
    with httpx2.Client(base_url=recorder.BASE_URL, transport=answer(200, body)) as client:
        path = recorder.record(client, recorder.RECORDINGS[3], tmp_path, replace=False)
    response = response_of(path)
    assert response["headers"] == {"content-type": "application/json", "x-ratelimit-limit": "40"}
    assert response["removed_fields"] == list(FIELDS)
    assert response["original_body_sha256"] == hashlib.sha256(body).hexdigest()
    assert (tmp_path / str(response["body_file"])).read_bytes() == recorded.encode()


def test_record_refuses_an_unexpected_status(tmp_path: Path) -> None:
    with (
        httpx2.Client(base_url=recorder.BASE_URL, transport=answer(429, b"{}")) as client,
        pytest.raises(recorder.RecordingError, match="429"),
    ):
        recorder.record(client, recorder.RECORDINGS[0], tmp_path, replace=False)
    assert list(tmp_path.iterdir()) == []


def test_record_keeps_earlier_recordings_unless_told_to_replace(tmp_path: Path) -> None:
    with httpx2.Client(base_url=recorder.BASE_URL, transport=answer(200, b"{}")) as client:
        recorder.record(client, recorder.RECORDINGS[0], tmp_path, replace=False)
        with pytest.raises(FileExistsError):
            recorder.record(client, recorder.RECORDINGS[0], tmp_path, replace=False)
        recorder.record(client, recorder.RECORDINGS[0], tmp_path, replace=True)


# Times and the command line.


@pytest.mark.parametrize(
    ("moment", "avoided"),
    [
        # 14 October 2026 is in summer time, two hours ahead of UTC.
        (datetime(2026, 10, 14, 2, 54, tzinfo=UTC), False),
        (datetime(2026, 10, 14, 2, 55, tzinfo=UTC), True),
        (datetime(2026, 10, 14, 6, 14, tzinfo=UTC), True),
        (datetime(2026, 10, 14, 6, 15, tzinfo=UTC), False),
        (datetime(2026, 10, 14, 9, 25, tzinfo=UTC), True),
        (datetime(2026, 10, 14, 9, 45, tzinfo=UTC), False),
        # 14 January 2027 is in winter time, one hour ahead of UTC.
        (datetime(2027, 1, 14, 2, 55, tzinfo=UTC), False),
        (datetime(2027, 1, 14, 3, 55, tzinfo=UTC), True),
    ],
)
def test_avoided_periods_follow_norwegian_time(moment: datetime, avoided: bool) -> None:
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


def test_main_refuses_to_run_in_an_avoided_period(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def always(moment: datetime) -> bool:
        return True

    monkeypatch.setattr(recorder, "in_avoided_period", always)
    assert recorder.main(["--out", str(tmp_path / "fixtures")]) == 1
    assert not (tmp_path / "fixtures").exists()


# Recordings for the reference plans of a question set.

TWO_YEARS = {
    "Kjonn": ["0"],
    "Alder": ["999B"],
    "ContentsCode": ["Personer"],
    "Tid": ["2025", "2026"],
}


def question(identifier: str, selection: dict[str, list[str]], language: str = "en") -> str:
    return json.dumps(
        {
            "id": identifier,
            "language": language,
            "text": "How many people lived in Norway?",
            "category": "lookup",
            "expected": {
                "outcome": "answer",
                "queries": [
                    {
                        "name": "Q1",
                        "table_id": "05810",
                        "selection": selection,
                        "language": language,
                    }
                ],
                "plan": {
                    "steps": [
                        {
                            "name": "total",
                            "operation": "value",
                            "snapshot": "Q1",
                            "codes": {
                                "Kjonn": "0",
                                "Alder": "999B",
                                "ContentsCode": "Personer",
                                "Tid": "2026",
                            },
                        }
                    ]
                },
                "numbers": [{"step": "total", "decimals": 0}],
            },
        }
    )


REFUSED = json.dumps(
    {
        "id": "out-of-scope-weather",
        "language": "no",
        "text": "Hvor varmt blir det i morgen?",
        "category": "out of scope",
        "expected": {"outcome": "refusal"},
    }
)


def question_file(tmp_path: Path, *lines: str) -> Path:
    path = tmp_path / "v1.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_a_question_s_query_is_recorded_as_the_client_will_send_it(tmp_path: Path) -> None:
    from opencited.sources.pxweb import normalise
    from opencited.sources.ssb import data_request

    selection = {**TWO_YEARS, "Tid": ["2026", " top( 2 ) "]}
    path = question_file(tmp_path, question("lookup-a", selection), REFUSED)

    metadata, data = recorder.question_recordings(path)

    async_client = httpx2.AsyncClient(base_url=recorder.BASE_URL)
    expected = data_request(async_client, "05810", normalise(selection), "en")
    with httpx2.Client(base_url=recorder.BASE_URL) as client:
        assert str(recorder.build(client, data).url) == str(expected.url)
        assert str(recorder.build(client, metadata).url) == (
            f"{recorder.BASE_URL}/tables/05810/metadata?lang=en"
        )
    assert (metadata.name, data.name) == ("metadata-05810-en", "question-lookup-a-q1")


def test_a_request_that_two_questions_send_is_recorded_once(tmp_path: Path) -> None:
    path = question_file(tmp_path, question("lookup-a", TWO_YEARS), question("lookup-b", TWO_YEARS))

    names = [item.name for item in recorder.question_recordings(path)]

    assert names == ["metadata-05810-en", "question-lookup-a-q1"]


def test_requests_that_fixtures_answer_are_not_sent_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The recorded query of table 05810 and its metadata are both in the fixtures.
    path = question_file(tmp_path, question("lookup-a", TWO_YEARS))

    def never(moment: datetime) -> bool:
        return False

    monkeypatch.setattr(recorder, "in_avoided_period", never)

    def refuse(*arguments: object, **options: object) -> Path:
        raise AssertionError("no request should be sent")

    monkeypatch.setattr(recorder, "record", refuse)

    assert recorder.main(["--questions", str(path), "--out", str(FIXTURES)]) == 0
    assert capsys.readouterr().out == ""
    with httpx2.Client(base_url=recorder.BASE_URL) as client:
        [_, data] = recorder.question_recordings(path)
        assert ("GET", str(recorder.build(client, data).url)) in recorder.recorded_requests(
            FIXTURES
        )


def test_a_question_set_that_cannot_be_read_stops_the_recorder(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = question_file(tmp_path, '{"id": "broken"}')

    assert recorder.main(["--questions", str(path), "--out", str(tmp_path)]) == 2
    assert "line 1" in capsys.readouterr().err

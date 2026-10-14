"""`opencited ask`: configuration, snapshots, language, output and exit status.

The run itself is replaced by an answer made from recorded evidence, or reaches an
endpoint that cannot be reached; no test here calls a model.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx2
import platformdirs
import pytest
from answering import answer
from recorded import FIXTURES

from opencited import cli
from opencited.agent.answer import Answer, Refusal
from opencited.agent.config import ModelConfig
from opencited.agent.model import ModelClient
from opencited.agent.render import text_of
from opencited.agent.tools import Toolbox
from opencited.snapshots import SnapshotStore
from opencited.sources import Language

KEY = "key-for-the-command-test"
# Kept before the fixture below replaces it for every test.
DEFAULT_SNAPSHOT_DIR = cli.default_snapshot_dir
ENDPOINT = {
    "OPENCITED_MODEL_URL": "https://models.example.org/v1",
    "OPENCITED_MODEL": "a-model",
    "OPENCITED_MODEL_KEY_VARIABLE": "A_KEY",
    "A_KEY": KEY,
}


@pytest.fixture(autouse=True)
def environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in ("OPENCITED_MODEL_URL", "OPENCITED_MODEL", "OPENCITED_MODEL_KEY_VARIABLE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv(cli.SNAPSHOT_DIR, raising=False)
    monkeypatch.setattr(cli, "default_snapshot_dir", lambda: tmp_path / "default-snapshots")


def configure(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in ENDPOINT.items():
        monkeypatch.setenv(name, value)


@dataclass
class Asked:
    """Stands in for the run, keeps what it was asked and gives a prepared result."""

    result: Answer | Refusal
    calls: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])

    async def __call__(
        self, question: str, language: Language, *, model: ModelClient, toolbox: Toolbox
    ) -> Answer | Refusal:
        self.calls.append({"question": question, "language": language, "toolbox": toolbox})
        return self.result


def refusal_of(result: Answer) -> Refusal:
    return Refusal(
        question=result.question,
        language=result.language,
        reason="limit",
        detail="the limit of 12 model calls",
        limit="model calls",
        explanation=None,
        report=None,
        attempts=(),
        model=result.model,
        prompt_version="1",
        usage=result.usage,
    )


def replaying(*arguments: str) -> list[str]:
    return ["ask", *arguments, "--replay", str(FIXTURES), "--no-cache"]


def test_without_an_endpoint_the_command_names_the_missing_variable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(replaying("Hvor mange bor i Norge?")) == 2
    assert capsys.readouterr().err.endswith("opencited: OPENCITED_MODEL_URL is not set\n")


def test_without_the_key_the_command_names_its_variable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    configure(monkeypatch)
    monkeypatch.delenv("A_KEY")

    assert cli.main(replaying("Hvor mange bor i Norge?")) == 2
    assert capsys.readouterr().err.endswith("opencited: the API key variable A_KEY is not set\n")


def test_a_snapshot_directory_that_cannot_be_written_stops_the_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    configure(monkeypatch)
    blocked = tmp_path / "a-file"
    blocked.write_text("not a directory")

    code = cli.main(replaying("Hvor mange bor i Norge?", "--snapshot-dir", str(blocked)))

    assert code == 2
    assert "snapshots cannot be written to" in capsys.readouterr().err


def test_a_run_that_fails_exits_with_1_and_the_reason(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    configure(monkeypatch)

    def unreachable(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("the endpoint is unreachable in tests", request=request)

    def client(config: ModelConfig) -> ModelClient:
        return ModelClient(config, transport=httpx2.MockTransport(unreachable))

    monkeypatch.setattr(cli, "ModelClient", client)

    assert cli.main(replaying("Hvor mange bor i Norge?")) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    [line] = captured.err.splitlines()
    assert re.fullmatch(
        r"opencited: the run failed: the endpoint could not be reached: Connection error\. "
        r"\(after 0 model calls, 0 tool calls, 0 tokens, [0-9]+\.[0-9]{3} s\)",
        line,
    )
    assert KEY not in captured.err


def test_an_answer_is_printed_with_numbered_sources(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    configure(monkeypatch)
    result, _, _ = answer(SnapshotStore(tmp_path / "evidence"))
    monkeypatch.setattr(cli, "ask", Asked(result))

    assert cli.main(replaying("Hvor mange bor i Norge?")) == 0
    assert capsys.readouterr().out == text_of(result) + "\n"


def test_json_prints_every_record_of_the_answer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    configure(monkeypatch)
    result, _, _ = answer(SnapshotStore(tmp_path / "evidence"))
    monkeypatch.setattr(cli, "ask", Asked(result))

    assert cli.main(replaying("Hvor mange bor i Norge?", "--json")) == 0
    printed = capsys.readouterr().out
    assert Answer.model_validate_json(printed) == result
    assert json.loads(printed)["citations"][0]["provenance"]["response_sha256"]
    assert KEY not in printed


def test_a_refusal_exits_with_3(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    configure(monkeypatch)
    result, _, _ = answer(SnapshotStore(tmp_path / "evidence"))
    refusal = refusal_of(result)
    monkeypatch.setattr(cli, "ask", Asked(refusal))

    assert cli.main(replaying("Hvor mange bor i Norge?")) == 3
    assert capsys.readouterr().out == text_of(refusal) + "\n"


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        (("Hvor mange bor i Norge?",), "no"),
        (("How many people live in Norway?",), "en"),
        (("How many people live in Norway?", "--language", "no"), "no"),
        (("Hvor mange bor i Norge?", "--language", "en"), "en"),
    ],
)
def test_the_answer_takes_the_language_of_the_question_unless_one_is_set(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    arguments: tuple[str, ...],
    expected: Language,
) -> None:
    configure(monkeypatch)
    result, _, _ = answer(SnapshotStore(tmp_path / "evidence"))
    asked = Asked(result)
    monkeypatch.setattr(cli, "ask", asked)

    cli.main(replaying(*arguments))

    [call] = asked.calls
    assert (call["question"], call["language"]) == (arguments[0], expected)
    assert call["toolbox"].language == expected


def test_snapshots_go_to_the_user_s_data_directory_unless_one_is_named(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configure(monkeypatch)
    result, _, _ = answer(SnapshotStore(tmp_path / "evidence"))
    asked = Asked(result)
    monkeypatch.setattr(cli, "ask", asked)
    named = tmp_path / "named"

    cli.main(replaying("Hvor mange bor i Norge?"))
    monkeypatch.setenv(cli.SNAPSHOT_DIR, str(tmp_path / "variable"))
    cli.main(replaying("Hvor mange bor i Norge?"))
    cli.main(replaying("Hvor mange bor i Norge?", "--snapshot-dir", str(named)))

    assert [call["toolbox"].store.directory for call in asked.calls] == [
        tmp_path / "default-snapshots",
        tmp_path / "variable",
        named,
    ]
    assert (tmp_path / "default-snapshots").is_dir()


def test_the_user_s_data_directory_holds_the_default_snapshots() -> None:
    expected = platformdirs.user_data_path("opencited", appauthor=False) / "snapshots"

    assert DEFAULT_SNAPSHOT_DIR() == expected


def test_an_unexpected_error_is_one_line_and_exit_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    configure(monkeypatch)

    async def broken(
        question: str, language: Language, *, model: ModelClient, toolbox: Toolbox
    ) -> Answer:
        raise RuntimeError("a fault in the package")

    monkeypatch.setattr(cli, "ask", broken)

    assert cli.main(replaying("Hvor mange bor i Norge?")) == 1
    assert capsys.readouterr().err.splitlines() == [
        "opencited: the run failed: an unexpected error: RuntimeError: a fault in the package"
    ]


def test_an_error_in_a_group_is_named_by_the_error_inside() -> None:
    group = ExceptionGroup("unhandled errors in a task group", [RuntimeError("inside")])

    error = cli.unexpected(ExceptionGroup("outer", [group]))

    assert str(error) == "an unexpected error: RuntimeError: inside"
    assert error.usage is None

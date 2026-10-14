"""The command line: its options, and how `main` starts the server with them."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import platformdirs
import pytest
from recorded import FIXTURES

from opencited import __version__, cli

type Run = tuple[tuple[Any, ...], dict[str, Any]]


@dataclass
class FakeServer:
    """Stands in for the server, and keeps how it was built and run."""

    built: dict[str, Any]
    runs: list[Run] = field(default_factory=list[Run])

    def run(self, *arguments: Any, **options: Any) -> None:
        self.runs.append((arguments, options))


@pytest.fixture
def servers(monkeypatch: pytest.MonkeyPatch) -> list[FakeServer]:
    built: list[FakeServer] = []

    def build(**options: Any) -> FakeServer:
        built.append(FakeServer(options))
        return built[-1]

    monkeypatch.setattr(cli, "build_server", build)
    monkeypatch.delenv(cli.SNAPSHOT_DIR, raising=False)
    return built


def test_stdio_is_the_default(servers: list[FakeServer]) -> None:
    assert cli.main(["mcp"]) == 0
    [server] = servers
    assert server.runs == [((), {})]


def test_the_cache_lies_in_the_user_s_cache_directory_by_default(
    servers: list[FakeServer],
) -> None:
    cli.main(["mcp"])
    [server] = servers
    expected = platformdirs.user_cache_path("opencited", appauthor=False) / "responses.sqlite"
    assert set(server.built) == {"cache", "snapshots"}
    assert server.built["snapshots"] is None
    assert server.built["cache"].path == expected


def test_the_cache_can_move(servers: list[FakeServer], tmp_path: Path) -> None:
    cli.main(["mcp", "--cache-dir", str(tmp_path)])
    [server] = servers
    assert server.built["cache"].path == tmp_path / "responses.sqlite"


def test_the_cache_can_be_turned_off(servers: list[FakeServer]) -> None:
    cli.main(["mcp", "--no-cache"])
    [server] = servers
    assert server.built == {"cache": None, "snapshots": None}


def test_a_cache_directory_and_no_cache_exclude_each_other(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as stopped:
        cli.parse_arguments(["mcp", "--no-cache", "--cache-dir", str(tmp_path)])
    assert stopped.value.code == 2


def test_http_binds_to_the_loopback_address_with_the_security_settings(
    servers: list[FakeServer],
) -> None:
    assert cli.main(["mcp", "--http"]) == 0
    [server] = servers
    assert server.runs == [
        (
            ("streamable-http",),
            {"host": "127.0.0.1", "port": 8765, "transport_security": cli.SECURITY},
        )
    ]


def test_the_security_settings_allow_loopback_names_only() -> None:
    assert cli.SECURITY.enable_dns_rebinding_protection
    assert cli.SECURITY.allowed_hosts == ["127.0.0.1:*", "localhost:*", "[::1]:*"]
    assert cli.SECURITY.allowed_origins == [
        "http://127.0.0.1:*",
        "http://localhost:*",
        "http://[::1]:*",
    ]


def test_replay_serves_recordings_without_the_revision_period(
    servers: list[FakeServer], caplog: pytest.LogCaptureFixture
) -> None:
    assert cli.main(["mcp", "--replay", str(FIXTURES)]) == 0
    [server] = servers
    assert set(server.built) == {"transport", "revision_period", "snapshots"}
    assert server.built["transport"] is not None
    assert server.built["revision_period"] is False
    assert "instead of calling SSB" in caplog.text


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "example.com"])  # noqa: S104 - the test checks that this address is refused
def test_the_command_serves_loopback_addresses_only(host: str) -> None:
    with pytest.raises(SystemExit) as stopped:
        cli.parse_arguments(["mcp", "--http", "--host", host])
    assert stopped.value.code == 2


@pytest.mark.parametrize("port", ["0", "65536", "http"])
def test_the_port_must_be_a_port_number(port: str) -> None:
    with pytest.raises(SystemExit) as stopped:
        cli.parse_arguments(["mcp", "--http", "--port", port])
    assert stopped.value.code == 2


def test_replay_needs_a_directory(tmp_path_factory: pytest.TempPathFactory) -> None:
    missing = tmp_path_factory.mktemp("replay") / "missing"
    with pytest.raises(SystemExit) as stopped:
        cli.parse_arguments(["mcp", "--replay", str(missing)])
    assert stopped.value.code == 2


def test_the_command_reports_its_version() -> None:
    finished = subprocess.run(
        [sys.executable, "-m", "opencited", "--version"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert finished.stdout.strip() == f"opencited {__version__}"


def test_snapshots_go_to_the_directory_the_option_names(
    servers: list[FakeServer], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(cli.SNAPSHOT_DIR, str(tmp_path / "from-variable"))
    cli.main(["mcp", "--snapshot-dir", str(tmp_path / "from-option")])
    [server] = servers
    assert server.built["snapshots"].directory == tmp_path / "from-option"


def test_snapshots_go_to_the_directory_the_variable_names(
    servers: list[FakeServer], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(cli.SNAPSHOT_DIR, str(tmp_path))
    cli.main(["mcp", "--replay", str(FIXTURES)])
    [server] = servers
    assert server.built["snapshots"].directory == tmp_path

"""Tests for the personal data check, run as the pre-commit hook runs it."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import check_personal_data as personal
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check_personal_data.py"


def run(*paths: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, paths)],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    ("line", "finding"),
    [
        ("Write to kari@gmail.com for access.", "email address outside the allowed domains"),
        ("Contact kari.nordmann@company.no", "email address outside the allowed domains"),
        ("The fixtures live in /home/kari/projects/opencited.", "path in a home directory"),
        ("Open /Users/kari/Downloads/table.json", "path in a home directory"),
        (r"Saved to C:\Users\kari\table.json", "path in a home directory"),
    ],
)
def test_finds_personal_data(line: str, finding: str) -> None:
    assert personal.line_findings(line) == [finding]


@pytest.mark.parametrize(
    "line",
    [
        "Author: 1+kari@users.noreply.github.com",
        "GitHub commits as noreply@github.com.",
        "Co-authored-by: Kari Nordmann <kari@example.org>",
        "uses: astral-sh/setup-uv@v10.3.0",
        "Clone git@github.com:owner/repository.git",
        "Write the file to /tmp/settings.json",
        "The runner home is /home/ on Linux.",
    ],
)
def test_accepts_text_without_personal_data(line: str) -> None:
    assert personal.line_findings(line) == []


def test_finds_the_name_of_this_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(personal.socket, "gethostname", lambda: "build-host-7.example.net")
    personal.host_pattern.cache_clear()
    try:
        assert personal.line_findings("Recorded on build-host-7 at noon") == [
            "name of this machine"
        ]
        assert personal.line_findings("Recorded on build-host-70 at noon") == []
    finally:
        personal.host_pattern.cache_clear()


def test_ignores_generic_host_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(personal.socket, "gethostname", lambda: "localhost")
    personal.host_pattern.cache_clear()
    try:
        assert personal.line_findings("Served on localhost") == []
    finally:
        personal.host_pattern.cache_clear()


def test_reports_the_line_without_the_matched_text(tmp_path: Path) -> None:
    path = tmp_path / "notes.md"
    path.write_text("First line.\n\nWrite to kari@gmail.com.\n", encoding="utf-8")
    result = run(path)
    assert result.returncode == 1
    assert result.stdout.strip() == f"{path}:3: email address outside the allowed domains"
    assert "kari" not in result.stdout + result.stderr


def test_skips_files_that_are_not_text(tmp_path: Path) -> None:
    path = tmp_path / "data.bin"
    path.write_bytes(b"\xff\xfe\x00kari@gmail.com")
    assert run(path).returncode == 0


def test_accepts_a_clean_file(tmp_path: Path) -> None:
    path = tmp_path / "notes.md"
    path.write_text("Values come from Statistics Norway.\n", encoding="utf-8")
    assert run(path).returncode == 0

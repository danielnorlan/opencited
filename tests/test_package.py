"""Smoke test for the installed package."""

import re

import opencited


def test_version_follows_pep_440() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+(?:\.dev\d+)?", opencited.__version__)

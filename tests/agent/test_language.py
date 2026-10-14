"""The language of a question."""

from __future__ import annotations

import pytest

from opencited.agent.language import ENGLISH, NORWEGIAN, language_of
from opencited.sources import Language


def test_no_word_counts_for_both_languages() -> None:
    assert not NORWEGIAN & ENGLISH


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("Hvor mange bor i Norge?", "no"),
        ("Hva var folketallet i Tromsø i 2025?", "no"),
        ("Hvor stor andel av befolkningen er over 80 år?", "no"),
        ("How many people live in Norway?", "en"),
        ("What share of the population is over 80?", "en"),
        ("What is the population of Bærum?", "en"),
        ("What was the population of Tromsø and Bodø in 2025?", "en"),
        ("Folketall Bærum", "no"),
        ("Population 2025", "en"),
        ("Befolkning 2025", "no"),
        ("2025", "no"),
        ("", "no"),
    ],
)
def test_the_language_with_more_of_its_words_wins_and_a_tie_is_norwegian(
    question: str, expected: Language
) -> None:
    assert language_of(question) == expected

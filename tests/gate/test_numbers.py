"""Reading numbers in answers, in Norwegian and English (spec 0003, tasks 1 and 2).

The texts are written for the tests: they are answers that a model could give, not
data from a source.
"""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from opencited.gate.numbers import Language, Number, read_numbers
from opencited.values import format_number

NBSP, NNBSP, THIN, MINUS, EN_DASH = "\u00a0", "\u202f", "\u2009", "\u2212", "\u2013"


def only(text: str, language: Language) -> Number:
    [number] = read_numbers(text, language)
    return number


def values(text: str, language: Language) -> list[tuple[str, Decimal | None]]:
    return [(number.kind, number.value) for number in read_numbers(text, language)]


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("5 627 400", "5627400"),
        (f"5{NBSP}627{NBSP}400", "5627400"),
        (f"5{NNBSP}627{NNBSP}400", "5627400"),
        (f"5{THIN}627{THIN}400", "5627400"),
        ("5627400", "5627400"),
        ("12,50", "12.50"),
        ("1 234,5", "1234.5"),
        ("0,059", "0.059"),
        ("2026", "2026"),
    ],
)
def test_norwegian_numbers_are_read_with_their_value_and_scale(text: str, value: str) -> None:
    number = only(f"Tallet er {text} i alt.", "no")
    assert number.kind == "number"
    assert number.value == Decimal(value)
    assert number.value is not None
    assert number.value.as_tuple().exponent == Decimal(value).as_tuple().exponent
    assert number.text == text


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("5,627,400", "5627400"),
        ("5627400", "5627400"),
        ("12.50", "12.50"),
        ("1,234.5", "1234.5"),
        ("0.059", "0.059"),
    ],
)
def test_english_numbers_are_read_with_their_value_and_scale(text: str, value: str) -> None:
    number = only(f"The number is {text} in all.", "en")
    assert (number.kind, number.value, number.text) == ("number", Decimal(value), text)
    assert number.value is not None
    assert number.value.as_tuple().exponent == Decimal(value).as_tuple().exponent


@pytest.mark.parametrize("language", ["no", "en"])
@pytest.mark.parametrize("minus", ["-", MINUS])
def test_a_minus_sign_counts_at_the_start_after_a_space_or_a_bracket(
    language: Language, minus: str
) -> None:
    for text in (f"{minus}3", f"falt {minus}3", f"({minus}3)"):
        assert values(text, language) == [("number", Decimal(-3))], text
    # After a letter, a hyphen is no minus sign.
    assert values(f"x{minus}3", language) == [("number", Decimal(3))]


@pytest.mark.parametrize("language", ["no", "en"])
@pytest.mark.parametrize("dash", ["-", EN_DASH])
def test_a_dash_between_two_numbers_marks_a_range(language: Language, dash: str) -> None:
    assert values(f"I 2020{dash}2023 var det slik.", language) == [
        ("number", Decimal(2020)),
        ("number", Decimal(2023)),
    ]
    both = read_numbers(f"mellom 10{dash}20 %", language)
    assert [(number.value, number.unit) for number in both] == [
        (Decimal(10), "per cent"),
        (Decimal(20), "per cent"),
    ]


@pytest.mark.parametrize(
    ("language", "text", "unit"),
    [
        ("no", "5 %", "per cent"),
        ("no", "5%", "per cent"),
        ("no", "5 prosent", "per cent"),
        ("no", "1,5 prosentpoeng", "percentage points"),
        ("en", "5 per cent", "per cent"),
        ("en", "5 percent", "per cent"),
        ("en", "5%", "per cent"),
        ("en", "1.5 percentage points", "percentage points"),
    ],
)
def test_percent_and_percentage_point_words_go_with_their_number(
    language: Language, text: str, unit: str
) -> None:
    number = only(f"Det er {text} her.", language)
    assert (number.unit, number.text) == (unit, text)


@pytest.mark.parametrize(
    ("language", "word", "power"),
    [
        ("no", "tusen", 3),
        ("no", "mill.", 6),
        ("no", "million", 6),
        ("no", "millioner", 6),
        ("no", "mrd.", 9),
        ("no", "milliard", 9),
        ("no", "milliarder", 9),
        ("en", "thousand", 3),
        ("en", "million", 6),
        ("en", "billion", 9),
    ],
)
def test_scale_words_go_with_their_number(language: Language, word: str, power: int) -> None:
    number = only(f"Det er 2{',' if language == 'no' else '.'}5 {word} her.", language)
    assert (number.value, number.scale) == (Decimal("2.5"), power)


@pytest.mark.parametrize(
    ("language", "text", "reason"),
    [
        ("no", "1e5", "exponent notation"),
        ("en", "1.5E+6", "exponent notation"),
        ("en", "1.5\u00d710^6", "exponent notation"),
        ("no", "2 \u00b7 10\u2076", "exponent notation"),
        ("en", "\u0663", "a digit other than 0 to 9"),
        ("en", "\uff13", "a digit other than 0 to 9"),
        ("no", "\u00b2", "a digit other than 0 to 9"),
        ("en", "1.234,5", "a mix of separators"),
        ("no", "1,234.5", "a mix of separators"),
        ("en", "12,5", "a comma that does not group digits in threes"),
        ("no", "12.5", "a point between digits in Norwegian"),
        ("no", "1.000", "a point between digits in Norwegian"),
        ("en", "1 000", "digits grouped by spaces in English text"),
        ("en", f"1{NBSP}000", "digits grouped by spaces in English text"),
        ("no", "3 billioner", "'billion' in Norwegian text"),
        ("no", "fem prosent", "a number written in words"),
        ("no", "ti tusen", "a number written in words"),
        ("no", "en million", "a number written in words"),
        ("en", "two million", "a number written in words"),
        ("en", "twenty-five percent", "a number written in words"),
        ("no", "doblet", "a word that multiplies"),
        ("no", "halvert", "a word that multiplies"),
        ("en", "doubled", "a word that multiplies"),
        ("en", "halved", "a word that multiplies"),
        ("en", "COVID19", "a number joined to letters"),
    ],
)
def test_every_blocking_form_blocks(language: Language, text: str, reason: str) -> None:
    found = read_numbers(f"Svaret er {text} her.", language)
    blocked = [number for number in found if number.kind == "blocked"]
    assert blocked, found
    assert reason in (blocked[0].reason or "")


@pytest.mark.parametrize(
    ("language", "text", "day"),
    [
        ("no", "2026-10-13", datetime.date(2026, 10, 13)),
        ("en", "2026-10-13", datetime.date(2026, 10, 13)),
        ("no", "13.10.2026", datetime.date(2026, 10, 13)),
        ("no", "1.3.2026", datetime.date(2026, 3, 1)),
        ("no", "13. oktober 2026", datetime.date(2026, 10, 13)),
        ("en", "13 October 2026", datetime.date(2026, 10, 13)),
        ("en", "October 13, 2026", datetime.date(2026, 10, 13)),
    ],
)
def test_dates_are_read_before_numbers(language: Language, text: str, day: datetime.date) -> None:
    number = only(f"Oppdatert {text}.", language)
    assert (number.kind, number.date, number.text) == ("date", day, text)


def test_a_date_that_does_not_exist_blocks() -> None:
    assert only("Oppdatert 31.02.2026.", "no").kind == "blocked"


@pytest.mark.parametrize(
    ("language", "text", "period"),
    [
        ("no", "2023K1", "2023K1"),
        ("no", "2023M01", "2023M01"),
        ("no", "1. kvartal 2023", "2023K1"),
        ("no", "fjerde kvartal 2023", "2023K4"),
        ("no", "januar 2023", "2023M01"),
        ("en", "first quarter of 2023", "2023K1"),
        ("en", "Q3 2023", "2023K3"),
        ("en", "January 2023", "2023M01"),
        ("en", "December 2023", "2023M12"),
    ],
)
def test_periods_are_read_as_ssb_writes_them(language: Language, text: str, period: str) -> None:
    number = only(f"I {text} var det slik.", language)
    assert (number.kind, number.period, number.text) == ("period", period, text)


def test_a_year_is_a_number_for_the_matching_to_place() -> None:
    assert values("I 2026 var det slik.", "no") == [("number", Decimal(2026))]


def test_a_table_id_keeps_its_leading_zero_in_the_text() -> None:
    number = only("Tabell 05810 viser det.", "no")
    assert (number.text, number.value) == ("05810", Decimal(5810))


def test_markers_and_list_numbers_are_told_apart() -> None:
    text = "1. Folketallet var 5 627 400 [E1].\n2. Andelen var 49,6 % [C12].\nIkke 3. linje."
    kinds = [(number.kind, number.text) for number in read_numbers(text, "no")]
    assert kinds == [
        ("list number", "1."),
        ("number", "5 627 400"),
        ("marker", "[E1]"),
        ("list number", "2."),
        ("number", "49,6 %"),
        ("marker", "[C12]"),
        ("number", "3"),
    ]


def test_the_text_is_normalised_to_nfc_first() -> None:
    # "kvartal" is plain ASCII; the decomposed "å" of "åtte" composes to "å".
    [number] = read_numbers("åtte prosent", "no")
    assert number.kind == "blocked"


def test_the_same_text_gives_the_same_numbers() -> None:
    text = "Folketallet var 5 627 400 i 2026 [E1], opp 0,59 prosent fra 1. kvartal 2025."
    assert read_numbers(text, "no") == read_numbers(text, "no")


@st.composite
def decimals(draw: st.DrawFn) -> Decimal:
    whole = draw(st.integers(-(10**38 - 1), 10**38 - 1))
    places = draw(st.integers(0, 12))
    return Decimal(f"{whole}E-{places}")


@given(decimals(), st.sampled_from(["no", "en"]))
def test_a_formatted_decimal_is_read_back_with_its_value_and_scale(
    value: Decimal, language: Language
) -> None:
    written = format_number(value, language)
    [number] = read_numbers(f"Verdien er {written}.", language)
    assert number.kind == "number", number
    assert number.value == value
    assert number.value is not None
    assert number.value.as_tuple().exponent == value.as_tuple().exponent
    assert number.text == written

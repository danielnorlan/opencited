"""Reading numbers in answers, in Norwegian and English (spec 0003, tasks 1 and 2).

The texts are written for the tests: they are answers that a model could give, not
data from a source.
"""

from __future__ import annotations

import datetime
import os
import subprocess
import sys
import time
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
    for text in (
        f"{minus}3",
        f"falt {minus}3",
        f"({minus}3)",
        f"[{minus}3]",
        f"**{minus}3**",
        f"|{minus}3|",
        f"_{minus}3_",
    ):
        assert values(text, language) == [("number", Decimal(-3))], text


@pytest.mark.parametrize("language", ["no", "en"])
@pytest.mark.parametrize("text", ["x-3", "COVID-19", '"-3"', "Endring:-3", "[E1]-3"])
def test_a_sign_that_may_not_be_a_minus_blocks(language: Language, text: str) -> None:
    found = read_numbers(text, language)
    assert [number.kind for number in found if number.kind != "marker"] == ["blocked"], found


@pytest.mark.parametrize("language", ["no", "en"])
@pytest.mark.parametrize("dash", ["\u2013", "\u2014"])
def test_a_dash_before_a_number_blocks(language: Language, dash: str) -> None:
    [number] = read_numbers(f"falt {dash}3 %", language)
    assert number.kind == "blocked"
    assert "no minus sign" in (number.reason or "")


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
        # Weeks, as SSB's weekly tables code them (#86).
        ("no", "2026U41", "2026U41"),
        ("no", "uke 41 2026", "2026U41"),
        ("no", "uke 7 i 2025", "2025U07"),
        ("no", "veke 41 2026", "2026U41"),
        ("en", "week 41 of 2026", "2026U41"),
        ("en", "week 09 2026", "2026U09"),
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
    exponent = draw(st.integers(-12, 3))
    return Decimal(f"{whole}E{exponent}")


def places(value: Decimal) -> int:
    """Return the decimals of a value as written in fixed point."""
    return max(0, -int(value.as_tuple().exponent))


@given(decimals(), st.sampled_from(["no", "en"]))
def test_a_formatted_decimal_is_read_back_with_its_value_and_scale(
    value: Decimal, language: Language
) -> None:
    written = format_number(value, language)
    [number] = read_numbers(f"Verdien er {written}.", language)
    assert number.kind == "number", number
    assert number.value == value
    assert number.value is not None
    assert places(number.value) == places(value)
    assert number.text == written


@pytest.mark.parametrize("language", ["no", "en"])
@pytest.mark.parametrize("markup", ["**{}**", "|{}|", "_{}_", "({})"])
def test_a_formatted_negative_decimal_in_markdown_keeps_its_sign(
    language: Language, markup: str
) -> None:
    written = format_number(Decimal("-1234567.5"), language)
    [number] = read_numbers(markup.format(written), language)
    assert number.value == Decimal("-1234567.5")


@pytest.mark.parametrize(
    ("language", "text", "kind", "found"),
    [
        ("no", "13. oktober 2026 var tallet", "date", "13. oktober 2026"),
        ("no", "1. kvartal 2023 steg det", "period", "1. kvartal 2023"),
        ("no", "1) Første punkt", "list number", "1)"),
    ],
)
def test_dates_and_periods_at_a_line_start_come_before_list_numbers(
    language: Language, text: str, kind: str, found: str
) -> None:
    number = read_numbers(text, language)[0]
    assert (number.kind, number.text) == (kind, found)


@pytest.mark.parametrize(
    "text",
    [
        "tjuefem prosent",
        "femtifem prosent",
        "tjueen tusen",
        "\u00e9n prosent",
        "en halv million",
        "fem%",
        "fem\u202fprosent",
        "fem mill",
        "tohundre prosent",
    ],
)
def test_norwegian_numbers_in_words_with_a_unit_block(text: str) -> None:
    blocked = [
        number for number in read_numbers(f"Det er {text} her.", "no") if number.kind == "blocked"
    ]
    assert blocked
    assert "in words" in (blocked[0].reason or "")


@pytest.mark.parametrize(
    "text",
    [
        "two million",
        "twenty-five percent",
        "a million",
        "an eighth percent",
        "half a million",
        "a quarter million",
    ],
)
def test_english_numbers_in_words_with_a_unit_block(text: str) -> None:
    found = read_numbers(f"It is {text} here.", "en")
    assert any(number.kind == "blocked" for number in found), found


@pytest.mark.parametrize(
    ("language", "text"),
    [
        ("no", "5 mill"),
        ("no", "5 mrd"),
        ("no", "5 mio."),
        ("en", "5 mln"),
        ("en", "5 bn"),
        ("en", "5 M"),
        ("en", "5 trillion"),
        ("no", "5 hundre"),
        ("no", "5-prosentig"),
        ("no", "5kr"),
    ],
)
def test_unit_and_scale_words_that_the_gate_does_not_read_block(
    language: Language, text: str
) -> None:
    [number] = read_numbers(f"Det er {text} her.", language)
    assert number.kind == "blocked", number


@pytest.mark.parametrize(
    ("language", "text", "unit"),
    [
        ("en", "1 percentage point", "percentage points"),
        ("no", "1,5 %-poeng", "percentage points"),
        ("no", "5\u00a0%", "per cent"),
        ("no", "5\u202fprosent", "per cent"),
    ],
)
def test_more_forms_of_percent_and_percentage_points_are_read(
    language: Language, text: str, unit: str
) -> None:
    [number] = read_numbers(f"Det er {text} her.", language)
    assert (number.kind, number.unit) == ("number", unit)


def test_a_scale_word_after_a_no_break_space_is_read() -> None:
    [number] = read_numbers("Det er 2,5\u00a0mill. her.", "no")
    assert (number.value, number.scale) == (Decimal("2.5"), 6)


def test_a_range_shares_its_scale_word() -> None:
    found = read_numbers("mellom 2-3 mill. personer", "no")
    assert [(number.value, number.scale) for number in found] == [(Decimal(2), 6), (Decimal(3), 6)]


@pytest.mark.parametrize(
    ("language", "word"),
    [
        ("no", "dobbelt"),
        ("no", "doblede"),
        ("no", "halverte"),
        ("no", "tidoblet"),
        ("no", "halvering"),
        ("en", "tenfold"),
        ("en", "triples"),
        ("en", "tripled"),
        ("en", "twofold"),
        ("en", "twice"),
    ],
)
def test_more_multiplying_words_block(language: Language, word: str) -> None:
    [number] = read_numbers(f"Tallet {word} seg.", language)
    assert number.kind == "blocked"


@pytest.mark.parametrize("text", ["10^6", "10**6", "2 \u00d7 10^3"])
def test_more_exponent_forms_block(text: str) -> None:
    [number] = read_numbers(f"Det er {text} her.", "en")
    assert (number.kind, number.text) == ("blocked", text)


@pytest.mark.parametrize(("language", "text"), [("no", "1.234.567"), ("en", "12,34,567")])
def test_a_blocked_number_is_blocked_whole(language: Language, text: str) -> None:
    [number] = read_numbers(f"Det er {text} her.", language)
    assert (number.kind, number.text) == ("blocked", text)


@pytest.mark.parametrize(
    ("language", "text", "kind", "found"),
    [
        ("no", "13.\u00a0oktober 2026", "date", datetime.date(2026, 10, 13)),
        ("no", "oktober\u00a02026", "period", "2026M10"),
        ("no", "1.\u00a0kvartal 2023", "period", "2023K1"),
        ("no", "1. kvartal i 2023", "period", "2023K1"),
    ],
)
def test_dates_and_periods_with_other_spaces_are_read(
    language: Language, text: str, kind: str, found: object
) -> None:
    [number] = read_numbers(f"Det var {text}.", language)
    assert number.kind == kind
    assert (number.date if kind == "date" else number.period) == found
    assert number.text == text


@pytest.mark.parametrize(
    ("language", "text"),
    [("no", "febuar 2023"), ("no", "maj 2023"), ("en", "may 2023"), ("no", "5. kvartal 2023")],
)
def test_words_that_are_no_month_or_quarter_leave_the_year_a_number(
    language: Language, text: str
) -> None:
    kinds = [number.kind for number in read_numbers(f"Det var {text}.", language)]
    assert "period" not in kinds
    assert "number" in kinds


@pytest.mark.parametrize(
    ("language", "text"), [("no", "uke 41"), ("no", "Uke 9"), ("en", "week 41")]
)
def test_a_week_without_a_year_is_read_as_its_number(language: Language, text: str) -> None:
    number = only(f"I {text} var det slik.", language)
    assert (number.kind, number.week, number.text) == ("week", int(text.split()[1]), text)


@pytest.mark.parametrize("text", ["uke 54 2026", "uke 0 2026", "per uke"])
def test_what_is_no_week_reads_no_week(text: str) -> None:
    kinds = {number.kind for number in read_numbers(f"Det var {text}.", "no")}
    assert not kinds & {"week", "period"}


@pytest.mark.parametrize("text", ["2023M13", "2026U54", "[E0]"])
def test_codes_that_are_no_period_or_marker_block(text: str) -> None:
    [number] = read_numbers(f"Se {text} her.", "no")
    assert number.kind == "blocked"


@pytest.mark.parametrize(
    ("text", "numbers"),
    [("5 1234", ["5", "1234"]), ("2025 300", ["2025", "300"]), ("12 34", ["12", "34"])],
)
def test_digits_after_a_space_group_only_in_threes_after_a_short_group(
    text: str, numbers: list[str]
) -> None:
    assert [number.value for number in read_numbers(f"Tall: {text}.", "no")] == [
        Decimal(n) for n in numbers
    ]


@pytest.mark.parametrize(("language", "text"), [("en", ".5"), ("no", ",5")])
def test_a_separator_without_a_digit_before_it_blocks(language: Language, text: str) -> None:
    [number] = read_numbers(f"Det er {text} her.", language)
    assert number.kind == "blocked"


def test_the_reader_takes_time_in_proportion_to_the_text() -> None:
    started = time.perf_counter()
    read_numbers("a " * 20000 + "prosent", "no")
    read_numbers("one-" * 10000 + "percent", "en")
    read_numbers("1 og " * 20000, "no")
    assert time.perf_counter() - started < 10


def test_the_same_text_gives_the_same_numbers_in_other_processes() -> None:
    script = (
        "import json, sys; from opencited.gate.numbers import read_numbers; "
        "print(json.dumps([n.model_dump(mode='json') for n in read_numbers(sys.argv[1], 'no')]))"
    )
    text = (
        "Folketallet var 5 627 400 i 2026 [E1], opp 0,59 prosent fra 1. kvartal 2025, fem prosent."
    )
    outputs = {
        subprocess.run(
            [sys.executable, "-c", script, text],
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, "PYTHONHASHSEED": seed},
        ).stdout
        for seed in ("0", "1", "2")
    }
    assert len(outputs) == 1


@pytest.mark.parametrize("text", ["2026-10-13T08:00:00Z", "2026-02-25T99:99:99+23:59"])
def test_a_time_of_day_blocks(text: str) -> None:
    [number] = read_numbers(f"Retrieved {text}.", "en")
    assert number.kind == "blocked"
    assert number.text == text


def test_a_number_at_a_line_start_counts_as_a_list_number_only_in_order() -> None:
    found = read_numbers("1. a\n2. b\n4. c\n9999999. d", "en")
    assert [(number.kind, number.text) for number in found] == [
        ("list number", "1."),
        ("list number", "2."),
        ("number", "4"),
        ("number", "9999999"),
    ]


@pytest.mark.parametrize(
    ("language", "text"),
    [("en", "5.6 million per cent"), ("no", "5,6 mill. %"), ("en", "2 thousand percentage points")],
)
def test_a_percent_word_after_a_scale_word_blocks(language: Language, text: str) -> None:
    [number] = read_numbers(f"It was {text}.", language)
    assert (number.kind, number.text) == ("blocked", text)


def test_a_number_of_more_digits_than_any_source_value_blocks() -> None:
    text = "0." + "1" * 5000
    [number] = read_numbers(f"It was {text}.", "en")
    assert number.kind == "blocked"
    assert number.text == text


@pytest.mark.parametrize(
    ("language", "text", "day"),
    [
        ("no", "Tallene gjelder befolkningen per 1. januar.", (1, 1)),
        ("no", "Tallene gjelder befolkningen per 1 januar.", (1, 1)),
        ("no", "Per 1. Januar var det slik.", (1, 1)),
        ("en", "The figures are for 1 January.", (1, 1)),
        ("no", "Skuddårsdagen 29. februar.", (2, 29)),
    ],
)
def test_a_day_and_month_without_a_year_is_a_day(
    language: Language, text: str, day: tuple[int, int]
) -> None:
    [number] = read_numbers(text, language)

    assert (number.kind, number.day) == ("day", day)


def test_a_day_with_its_year_stays_a_date_and_a_month_with_its_year_a_period() -> None:
    kinds = [number.kind for number in read_numbers("1. januar 2026 og januar 2026.", "no")]

    assert kinds == ["date", "period"]


def test_a_day_that_no_year_has_blocks_and_an_english_month_needs_its_capital() -> None:
    [blocked] = read_numbers("Per 31. februar.", "no")
    [number] = read_numbers("On 1 january.", "en")

    assert (blocked.kind, blocked.reason) == ("blocked", "a date that does not exist")
    assert number.kind == "number"


@pytest.mark.parametrize(
    ("language", "text"),
    [
        ("en", "Highest months:\n\n1. August [E1]\n2. July [E1]\n3. June [E1]"),
        ("no", "Kaldeste måneder:\n\n1. januar [E1]\n2. februar [E1]"),
    ],
)
def test_a_list_whose_items_start_with_a_month_stays_a_list(language: Language, text: str) -> None:
    kinds = [number.kind for number in read_numbers(text, language) if number.kind != "marker"]

    assert kinds == ["list number"] * len(kinds)


def test_a_letter_that_folds_to_a_month_s_letter_makes_no_month() -> None:
    # The long s, U+017F, folds to s, so a case-insensitive match would read the month
    # below as August.
    month = "augu\u017ft"
    found = read_numbers(f"Per 5 {month} og 6. {month} 2026, i {month} 2026.", "no")

    assert {number.kind for number in found} == {"number"}

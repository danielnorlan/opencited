"""Reading the numbers in an answer, in Norwegian or English (spec 0003, tasks 1 and 2).

The tokenizer normalises the text to Unicode NFC and finds, in this order, citation
markers, dates, periods, the numbers of ordered list items, days and months without a
year, the forms that block an answer, and other numbers. Each pass claims its span of text, so a later pass never
reads a digit that an earlier one read. It uses no model and no network. Positions are
offsets in ``normalise(text)``.

A number is read with its value and its decimals, ``12,50`` as ``Decimal("12.50")``,
and with any percent, percentage point or scale word after it. Whether a number is a
value, a period, a table ID or a number from a label is decided when it is matched
against the cited results, since only they say which years and IDs they hold.

The gate is a check, so a form it cannot read with certainty blocks the answer with a
reason instead of being read one way or another. Beyond the blocking forms of spec
0003 this covers a number joined to letters, a sign or dash where spec 0003 reads no
minus sign, and unit and scale words that the specification does not list.
"""

from __future__ import annotations

import datetime
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from opencited.model import Frozen
from opencited.values import ExactDecimal

type Language = Literal["no", "en"]
type Kind = Literal["number", "date", "day", "week", "period", "marker", "list number", "blocked"]
type Unit = Literal["per cent", "percentage points"]

# Spaces that group digits in Norwegian text (spec 0003), which may also stand between
# a number and its word, and between the parts of a date or a period.
GROUP_SPACES = " \u00a0\u202f\u2009"
SPACE = "[ \u00a0\u202f\u2009]"
# A week's number, 1 to 53, with or without a leading zero.
WEEK = "(0?[1-9]|[1-4][0-9]|5[0-3])"
# The words for a week: Norwegian bokmål and nynorsk, and English.
WEEK_WORDS = "(?i:uke|veke|week)"
MINUS = "-\u2212"
RANGE_DASHES = "-\u2013"
OTHER_DASHES = "\u2013\u2014\u2012"
# Before a minus sign: an opening bracket, as spec 0003 says, or Markdown markup, which
# is not text. Elsewhere a sign before a digit cannot be read with certainty.
BEFORE_MINUS = "([{*_|~>"
ASCII_DIGITS = "0123456789"
# A source value has at most 38 digits; a written number may round it or add zeros,
# and no number in an answer needs more than this many.
MAX_DIGITS = 60

MONTHS_NO = (
    "januar",
    "februar",
    "mars",
    "april",
    "mai",
    "juni",
    "juli",
    "august",
    "september",
    "oktober",
    "november",
    "desember",
)
# English months are written with a capital, which keeps "may" the verb out.
MONTHS_EN = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
QUARTER_WORDS_NO = ("første", "andre", "tredje", "fjerde")
QUARTER_WORDS_EN = ("first", "second", "third", "fourth")

PER_CENT_WORDS = ("prosent", "per cent", "percent")
POINT_WORDS = ("prosentpoeng", "percentage points", "percentage point")
SCALE_WORDS: dict[Language, dict[str, int]] = {
    "no": {
        "tusen": 3,
        "mill.": 6,
        "million": 6,
        "millioner": 6,
        "mrd.": 9,
        "milliard": 9,
        "milliarder": 9,
    },
    "en": {"thousand": 3, "million": 6, "billion": 9},
}
# Words for a unit or a scale that the specification does not list. After a number they
# block the answer, since reading the number without them would change its size.
UNREAD_WORDS = (
    "mill",
    "mrd",
    "mio",
    "mio.",
    "mln",
    "mln.",
    "bn",
    "bln",
    "mn",
    "trillion",
    "trillions",
    "trillioner",
    "billiard",
    "billiarder",
    "hundre",
    "hundred",
    "millions",
    "billions",
    "thousands",
    "k",
    "K",
    "M",
    "B",
)
NUMBER_WORDS = frozenset(
    {
        "null",
        "en",
        "én",
        "ett",
        "ei",
        "to",
        "tre",
        "fire",
        "fem",
        "seks",
        "sju",
        "syv",
        "åtte",
        "ni",
        "ti",
        "elleve",
        "tolv",
        "tretten",
        "fjorten",
        "femten",
        "seksten",
        "sytten",
        "atten",
        "nitten",
        "tjue",
        "tyve",
        "tretti",
        "tredve",
        "førti",
        "femti",
        "seksti",
        "sytti",
        "åtti",
        "nitti",
        "hundre",
        "tusen",
        "halv",
        "halvt",
        "halvannen",
        "a",
        "an",
        "zero",
        "one",
        "two",
        "three",
        "four",
        "five",
        "six",
        "seven",
        "eight",
        "nine",
        "ten",
        "eleven",
        "twelve",
        "thirteen",
        "fourteen",
        "fifteen",
        "sixteen",
        "seventeen",
        "eighteen",
        "nineteen",
        "twenty",
        "thirty",
        "forty",
        "fifty",
        "sixty",
        "seventy",
        "eighty",
        "ninety",
        "hundred",
        "thousand",
        "half",
        # Fractions, which make a number in words as well.
        "kvart",
        "tredjedel",
        "fjerdedel",
        "femtedel",
        "tidel",
        "quarter",
        "third",
        "fifth",
        "sixth",
        "seventh",
        "eighth",
        "ninth",
        "tenth",
        "hundredth",
        "thousandth",
    }
)
# Norwegian writes tens and units, and hundreds, as one word: "tjuefem", "tohundre".
NUMBER_COMPOUND = re.compile(
    r"(?:(?:tjue|tyve|tretti|tredve|førti|femti|seksti|sytti|åtti|nitti)"
    r"(?:en|ett|to|tre|fire|fem|seks|sju|syv|åtte|ni)|"
    r"(?:to|tre|fire|fem|seks|sju|syv|åtte|ni)?hundre\w*|\w+tusen)",
    re.IGNORECASE,
)
# Words that multiply a number, which the gate cannot check (spec 0003).
MULTIPLYING = re.compile(
    r"\b(?:\w*dobl\w*|dobbel\w*|\w*halver\w*|doubl\w*|tripl\w*|quadrupl\w*|"
    r"halve|halved|halves|halving|twice|thrice|"
    r"(?:two|three|four|five|six|seven|eight|nine|ten|hundred|thousand)fold)\b",
    re.IGNORECASE,
)
MARKER = re.compile(r"\[(?:E|C)[1-9][0-9]*\]")
LIST_NUMBER = re.compile(r"(?m)^[ \t]*([0-9]+)[.)](?=[ \t])")


class Number(Frozen):
    """A number found in an answer.

    ``position`` and ``end`` are offsets in the normalised text. ``value`` is the number
    as written, with its decimals, before any scale word; ``scale`` is the power of ten
    of a scale word after it. ``unit`` says whether a percent or percentage point word
    follows. A date has ``date``, a day and month without a year ``day`` as (month,
    day), a week without a year ``week``, a period ``period`` as SSB writes it, such as
    ``2023K1`` or ``2023U41``, and a blocked form ``reason``.
    """

    position: int
    end: int
    text: str
    kind: Kind
    value: ExactDecimal | None = None
    scale: int = 0
    unit: Unit | None = None
    date: datetime.date | None = None
    day: tuple[int, int] | None = None
    week: int | None = None
    period: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class Read:
    digits: str
    end: int


@dataclass(frozen=True)
class Blocked:
    reason: str
    end: int


@dataclass(frozen=True)
class After:
    unit: Unit | None
    scale: int
    end: int


def normalise(text: str) -> str:
    """Return the text as the reader reads it, in Unicode NFC."""
    return unicodedata.normalize("NFC", text)


def alternatives(words: tuple[str, ...] | list[str]) -> str:
    return "(?:" + "|".join(re.escape(word) for word in sorted(words, key=len, reverse=True)) + ")"


def date_patterns() -> list[tuple[re.Pattern[str], str]]:
    norwegian = alternatives(MONTHS_NO)
    english = alternatives(MONTHS_EN)
    return [
        (
            re.compile(
                r"(?<![\w.])([0-9]{4})-([0-9]{2})-([0-9]{2})"
                r"(?:T[0-9]{2}:[0-9]{2}(?::[0-9]{2}(?:\.[0-9]+)?)?(?:Z|[+-][0-9]{2}:?[0-9]{2})?)?"
                r"(?![\w-])"
            ),
            "iso",
        ),
        (
            re.compile(r"(?<![\w.,])([0-9]{1,2})\.([0-9]{1,2})\.([0-9]{4})(?![\w]|\.[0-9])"),
            "dotted",
        ),
        (
            re.compile(
                rf"(?<![\w.,])([0-9]{{1,2}})\.?{SPACE}((?ai:{norwegian})){SPACE}([0-9]{{4}})(?!\w)"
            ),
            "day month",
        ),
        (
            re.compile(rf"(?<![\w.,])([0-9]{{1,2}}){SPACE}({english}){SPACE}([0-9]{{4}})(?!\w)"),
            "day month",
        ),
        (
            re.compile(rf"(?<!\w)({english}){SPACE}([0-9]{{1,2}}),{SPACE}([0-9]{{4}})(?!\w)"),
            "month day",
        ),
    ]


def day_patterns() -> list[re.Pattern[str]]:
    """Return the patterns of a day and month without a year, in either language."""
    norwegian = alternatives(MONTHS_NO)
    english = alternatives(MONTHS_EN)
    return [
        re.compile(rf"(?<![\w.,])([0-9]{{1,2}})\.?{SPACE}((?ai:{norwegian}))(?!\w)"),
        re.compile(rf"(?<![\w.,])([0-9]{{1,2}}){SPACE}({english})(?!\w)"),
    ]


def month_number(name: str) -> int:
    lowered = name.lower()
    if lowered in MONTHS_NO:
        return MONTHS_NO.index(lowered) + 1
    return [month.lower() for month in MONTHS_EN].index(lowered) + 1


def read_date(kind: str, groups: tuple[str, ...]) -> datetime.date | None:
    try:
        match kind:
            case "iso":
                return datetime.date(int(groups[0]), int(groups[1]), int(groups[2]))
            case "dotted":
                return datetime.date(int(groups[2]), int(groups[1]), int(groups[0]))
            case "day month":
                return datetime.date(int(groups[2]), month_number(groups[1]), int(groups[0]))
            case _:
                return datetime.date(int(groups[2]), month_number(groups[0]), int(groups[1]))
    except ValueError:
        return None


def period_patterns() -> list[tuple[re.Pattern[str], str]]:
    norwegian = alternatives(MONTHS_NO)
    english = alternatives(MONTHS_EN)
    quarter_no = alternatives(QUARTER_WORDS_NO)
    quarter_en = alternatives(QUARTER_WORDS_EN)
    return [
        (re.compile(r"(?<!\w)([0-9]{4})K([1-4])(?!\w)"), "code quarter"),
        (re.compile(r"(?<!\w)([0-9]{4})M(0[1-9]|1[0-2])(?!\w)"), "code month"),
        (re.compile(r"(?<!\w)([0-9]{4})U(0[1-9]|[1-4][0-9]|5[0-3])(?!\w)"), "code week"),
        (
            re.compile(
                rf"(?<!\w){WEEK_WORDS}{SPACE}{WEEK}{SPACE}(?:(?i:i|in|of){SPACE})?([0-9]{{4}})(?!\w)"
            ),
            "number week",
        ),
        (
            re.compile(
                rf"(?<![\w.,])([1-4])\.{SPACE}(?i:kvartal){SPACE}(?:i{SPACE})?([0-9]{{4}})(?!\w)"
            ),
            "number quarter",
        ),
        (
            re.compile(
                rf"(?<!\w)((?i:{quarter_no})){SPACE}(?i:kvartal){SPACE}(?:i{SPACE})?([0-9]{{4}})(?!\w)"
            ),
            "word quarter",
        ),
        (
            re.compile(
                rf"(?<!\w)((?i:{quarter_en})){SPACE}(?i:quarter){SPACE}(?:of{SPACE})?([0-9]{{4}})(?!\w)"
            ),
            "word quarter",
        ),
        (re.compile(rf"(?<!\w)Q([1-4]){SPACE}([0-9]{{4}})(?!\w)"), "number quarter"),
        (
            re.compile(rf"(?<!\w)((?ai:{norwegian})){SPACE}(?:i{SPACE})?([0-9]{{4}})(?!\w)"),
            "month",
        ),
        (re.compile(rf"(?<!\w)({english}){SPACE}(?:of{SPACE})?([0-9]{{4}})(?!\w)"), "month"),
    ]


def read_period(kind: str, groups: tuple[str, ...]) -> str:
    match kind:
        case "code quarter":
            return f"{groups[0]}K{groups[1]}"
        case "code month":
            return f"{groups[0]}M{groups[1]}"
        case "code week":
            return f"{groups[0]}U{groups[1]}"
        case "number week":
            return f"{groups[1]}U{int(groups[0]):02d}"
        case "number quarter":
            return f"{groups[1]}K{groups[0]}"
        case "word quarter":
            word = groups[0].lower()
            words = QUARTER_WORDS_NO if word in QUARTER_WORDS_NO else QUARTER_WORDS_EN
            return f"{groups[1]}K{words.index(word) + 1}"
        case _:
            return f"{groups[1]}M{month_number(groups[0]):02d}"


def is_number_word(word: str) -> bool:
    return word.lower() in NUMBER_WORDS or NUMBER_COMPOUND.fullmatch(word) is not None


class Reader:
    """Reads the numbers of one text in one language."""

    def __init__(self, text: str, language: Language) -> None:
        self.text = normalise(text)
        self.language: Language = language
        self.claimed = bytearray(len(self.text))
        self.found: list[Number] = []

    def free(self, start: int, end: int) -> bool:
        return not any(self.claimed[start:end])

    def add(self, number: Number) -> None:
        self.found.append(number)
        self.claimed[number.position : number.end] = b"\x01" * (number.end - number.position)

    def block(self, start: int, end: int, reason: str) -> None:
        self.add(
            Number(
                position=start, end=end, text=self.text[start:end], kind="blocked", reason=reason
            )
        )

    def read(self) -> tuple[Number, ...]:
        self.markers()
        self.dates()
        self.periods()
        # After list numbers, so that a list such as "1. August" and "2. July" stays one.
        self.list_numbers()
        self.days()
        self.weeks()
        self.blocking_forms()
        self.numbers()
        return tuple(sorted(self.found, key=lambda number: (number.position, number.end)))

    def markers(self) -> None:
        for match in MARKER.finditer(self.text):
            self.add(Number(position=match.start(), end=match.end(), text=match[0], kind="marker"))

    def dates(self) -> None:
        for pattern, kind in date_patterns():
            for match in pattern.finditer(self.text):
                if not self.free(*match.span()):
                    continue
                when = read_date(kind, match.groups())
                if when is None:
                    self.block(*match.span(), "a date that does not exist")
                elif kind == "iso" and "T" in match[0]:
                    self.block(*match.span(), "a time of day, which the gate cannot check")
                else:
                    self.add(
                        Number(
                            position=match.start(),
                            end=match.end(),
                            text=match[0],
                            kind="date",
                            date=when,
                        )
                    )

    def days(self) -> None:
        """Read a day and month without a year, such as "1. januar" or "1 January"."""
        for pattern in day_patterns():
            for match in pattern.finditer(self.text):
                if not self.free(*match.span()):
                    continue
                month, day = month_number(match[2]), int(match[1])
                try:
                    # 2000 was a leap year, so 29 February is a day.
                    datetime.date(2000, month, day)
                except ValueError:
                    self.block(*match.span(), "a date that does not exist")
                    continue
                self.add(
                    Number(
                        position=match.start(),
                        end=match.end(),
                        text=match[0],
                        kind="day",
                        day=(month, day),
                    )
                )

    def weeks(self) -> None:
        """Read a week without a year, such as "uke 41" or "week 41"."""
        pattern = re.compile(rf"(?<!\w){WEEK_WORDS}{SPACE}{WEEK}(?!\w|[.,][0-9])")
        for match in pattern.finditer(self.text):
            if self.free(*match.span()):
                self.add(
                    Number(
                        position=match.start(),
                        end=match.end(),
                        text=match[0],
                        kind="week",
                        week=int(match[1]),
                    )
                )

    def periods(self) -> None:
        for pattern, kind in period_patterns():
            for match in pattern.finditer(self.text):
                if self.free(*match.span()):
                    self.add(
                        Number(
                            position=match.start(),
                            end=match.end(),
                            text=match[0],
                            kind="period",
                            period=read_period(kind, match.groups()),
                        )
                    )

    def list_numbers(self) -> None:
        """Mark the numbers of ordered list items: a list starts at 1 and counts up.

        Any other number at the start of a line is read as a number, so a value cannot
        pass as the number of a list item.
        """
        expected = 1
        for match in LIST_NUMBER.finditer(self.text):
            start, end = match.start(1), match.end(1) + 1
            value = int(match[1]) if len(match[1]) < 10 else -1
            if value == 1:
                expected = 1
            if value != expected or not self.free(start, end):
                continue
            expected += 1
            self.add(Number(position=start, end=end, text=self.text[start:end], kind="list number"))

    def blocking_forms(self) -> None:
        for match in MULTIPLYING.finditer(self.text):
            if self.free(*match.span()):
                self.block(
                    *match.span(), "a word that multiplies a number, which the gate cannot check"
                )
        self.words_with_units()
        for index, char in enumerate(self.text):
            if (
                char not in ASCII_DIGITS
                and unicodedata.category(char) in ("Nd", "No")
                and self.free(index, index + 1)
            ):
                self.block(index, index + 1, "a digit other than 0 to 9")
        if self.language == "no":
            for match in re.finditer(r"\bbillion(?:er)?\b", self.text, re.IGNORECASE):
                if self.free(*match.span()):
                    self.block(
                        *match.span(), "'billion' in Norwegian text, which can mean 10^9 or 10^12"
                    )

    def words_with_units(self) -> None:
        """Block a number in words with a percent or scale word after it.

        Each unit word is checked against at most 80 characters before it, so the time
        grows with the length of the text however the words repeat.
        """
        units = [
            *PER_CENT_WORDS,
            *POINT_WORDS,
            *SCALE_WORDS["no"],
            *SCALE_WORDS["en"],
            *UNREAD_WORDS,
        ]
        pattern = re.compile(rf"(?:%|(?<![\w.]){alternatives(units)}(?!\w))", re.IGNORECASE)
        before = re.compile(rf"((?:[^\W\d_]+[ -])*[^\W\d_]+){SPACE}?$")
        for match in pattern.finditer(self.text):
            prefix = self.text[max(0, match.start() - 80) : match.start()]
            words = before.search(prefix)
            if words is None:
                continue
            start = match.start() - len(prefix) + words.start(1)
            run_start: int | None = None
            offset = start
            for part in re.split(r"([ -])", words[1]):
                if part in (" ", "-"):
                    offset += 1
                    continue
                if is_number_word(part):
                    run_start = offset if run_start is None else run_start
                else:
                    run_start = None
                offset += len(part)
            # A scale word after digits belongs to the number that the digits start.
            digits_before = re.search(rf"[0-9]{SPACE}?$", self.text[: run_start or 0])
            if (
                run_start is not None
                and digits_before is None
                and self.free(run_start, match.end())
            ):
                self.block(
                    run_start, match.end(), "a number written in words with a percent or scale word"
                )

    def numbers(self) -> None:
        index = 0
        while index < len(self.text):
            if self.text[index] in ASCII_DIGITS and self.free(index, index + 1):
                index = self.number_at(index)
            else:
                index += 1

    def number_at(self, start: int) -> int:
        """Read the number that starts at ``start`` and return the index after it."""
        text = self.text
        previous = text[start - 1] if start > 0 else ""
        if previous.isalpha():
            begin = start
            while begin > 0 and text[begin - 1].isalnum():
                begin -= 1
            end = self.word_end(start)
            self.block(begin, end, "a number joined to letters")
            return end
        if previous in (".", ",") and (start == 1 or not text[start - 2].isalnum()):
            end = self.run_end(start)
            self.block(start - 1, end, "a decimal separator without a digit before it")
            return end
        begin, negative = start, False
        if previous and (previous in MINUS or previous in OTHER_DASHES):
            before = text[start - 2] if start > 1 else ""
            if before.isdigit():
                end = self.run_end(start)
                self.block(start - 1, end, "a dash between numbers that the gate cannot read")
                return end
            if previous in OTHER_DASHES:
                end = self.run_end(start)
                self.block(start - 1, end, "a dash before a number, which is no minus sign")
                return end
            if before == "" or before.isspace() or before in BEFORE_MINUS:
                begin, negative = start - 1, True
            else:
                end = self.run_end(start)
                self.block(max(0, start - 2), end, "a sign that may or may not be a minus sign")
                return end
        first = self.parse(start)
        if isinstance(first, Blocked):
            self.block(begin, first.end, first.reason)
            return first.end
        ends = [(begin, first, negative)]
        end = first.end
        if end + 1 < len(text) and text[end] in RANGE_DASHES and text[end + 1] in ASCII_DIGITS:
            second = self.parse(end + 1)
            if isinstance(second, Blocked):
                self.block(begin, second.end, second.reason)
                return second.end
            ends.append((end + 1, second, False))
            end = second.end
        after = self.trailing(end)
        if isinstance(after, Blocked):
            self.block(begin, after.end, after.reason)
            return after.end
        for index, (position, read, minus) in enumerate(ends):
            closing = after.end if index == len(ends) - 1 else read.end
            self.add(
                Number(
                    position=position,
                    end=closing,
                    text=text[position:closing],
                    kind="number",
                    value=Decimal(("-" if minus else "") + read.digits),
                    scale=after.scale,
                    unit=after.unit,
                )
            )
        return after.end

    def run(self, start: int) -> int:
        """Return the end of the digits 0 to 9 that start at ``start``."""
        end = start
        while end < len(self.text) and self.text[end] in ASCII_DIGITS:
            end += 1
        return end

    def run_end(self, start: int) -> int:
        """Return the end of the digits and the separators between them at ``start``."""
        end = self.run(start)
        while (
            end + 1 < len(self.text)
            and self.text[end] in ".,"
            and self.text[end + 1] in ASCII_DIGITS
        ):
            end = self.run(end + 1)
        return end

    def word_end(self, start: int) -> int:
        """Return the end of the letters and digits that start at ``start``."""
        end = start
        while end < len(self.text) and self.text[end].isalnum():
            end += 1
        return end

    def parse(self, start: int) -> Read | Blocked:
        """Read the digits, groups and decimals at ``start`` as the language writes them."""
        text = self.text
        size = len(text)
        end = self.run(start)
        digits = text[start:end]
        grouping = GROUP_SPACES if self.language == "no" else ","
        point = "," if self.language == "no" else "."

        def digit(at: int) -> bool:
            return at < size and text[at] in ASCII_DIGITS

        # A first group of one to three digits can be followed by groups of three.
        if len(digits) <= 3:
            while end < size and text[end] in grouping and digit(end + 1):
                stop = self.run(end + 1)
                if stop - (end + 1) != 3:
                    break
                digits += text[end + 1 : stop]
                end = stop
        if self.language == "en":
            if end < size and text[end] == "," and digit(end + 1):
                return Blocked(
                    "a comma that does not group digits in threes, which fits neither language",
                    self.run_end(end + 1),
                )
            if end < size and text[end] in GROUP_SPACES and digit(end + 1):
                stop = self.run(end + 1)
                if len(digits) <= 3 and stop - (end + 1) == 3:
                    return Blocked("digits grouped by spaces in English text", self.run_end(stop))
        if end < size and text[end] == point and digit(end + 1):
            stop = self.run(end + 1)
            digits = f"{digits}.{text[end + 1 : stop]}"
            end = stop
            if end < size and text[end] in ".," and digit(end + 1):
                return Blocked(
                    "a mix of separators that fits neither language", self.run_end(end + 1)
                )
        if self.language == "no" and end < size and text[end] == "." and digit(end + 1):
            return Blocked(
                "a point between digits in Norwegian text outside a date", self.run_end(end + 1)
            )
        if len(digits.replace(".", "")) > MAX_DIGITS:
            return Blocked(
                f"a number of more than {MAX_DIGITS} digits, more than any source value has",
                self.run_end(end),
            )
        return Read(digits, end)

    def trailing(self, end: int) -> After | Blocked:
        """Read the percent, percentage point or scale word after a number, if any."""
        rest = self.text[end:]
        exponent = (
            re.match(r"[eE][+\u2212-]?[0-9]+", rest)
            or re.match(r"(?:\^|\*\*)[+\u2212-]?[0-9]+", rest)
            or re.match(
                r"[ \u00a0]?[\u00d7x*\u00b7][ \u00a0]?10"
                r"(?:\^[+\u2212-]?[0-9]+|\*\*[0-9]+|[\u2070-\u2079\u00b9\u00b2\u00b3]+)",
                rest,
            )
        )
        if exponent:
            return Blocked("exponent notation", end + exponent.end())
        known = {*PER_CENT_WORDS, *POINT_WORDS, *SCALE_WORDS[self.language]}
        if rest[:1].isalpha():
            glued = re.match(r"[^\W\d_]+\.?", rest)
            word = glued[0].lower() if glued else ""
            if word not in known:
                return Blocked("a number joined to letters", self.word_end(end))
        if re.match(r"-[^\W\d_]", rest):
            return Blocked("a number joined to letters", self.word_end(end + 1))
        space = re.match(SPACE + "?", rest)
        offset = space.end() if space else 0
        after = rest[offset:]
        if after.startswith("%-poeng"):
            return After("percentage points", 0, end + offset + len("%-poeng"))
        if after.startswith("%"):
            return After("per cent", 0, end + offset + 1)
        lowered = after.lower()

        def ends_word(word: str) -> bool:
            if len(word) == 1:
                return after.startswith(word) and not after[1:2].isalnum()
            return lowered.startswith(word.lower()) and (
                word.endswith(".") or not lowered[len(word) : len(word) + 1].isalpha()
            )

        for word in sorted(POINT_WORDS, key=len, reverse=True):
            if ends_word(word):
                return After("percentage points", 0, end + offset + len(word))
        for word in PER_CENT_WORDS:
            if ends_word(word):
                return After("per cent", 0, end + offset + len(word))
        for word, power in sorted(
            SCALE_WORDS[self.language].items(), key=lambda item: -len(item[0])
        ):
            if ends_word(word):
                stop = end + offset + len(word)
                percent = re.match(
                    rf"{SPACE}?(?:%|{alternatives([*POINT_WORDS, *PER_CENT_WORDS])})",
                    self.text[stop:],
                    re.IGNORECASE,
                )
                if percent:
                    return Blocked("a percent word after a scale word", stop + percent.end())
                return After(None, power, stop)
        for word in sorted(UNREAD_WORDS, key=len, reverse=True):
            if ends_word(word):
                return Blocked(
                    f"the word {word!r}, a unit or scale that the gate does not read",
                    end + offset + len(word),
                )
        return After(None, 0, end)


def read_numbers(text: str, language: Language) -> tuple[Number, ...]:
    """Return every number in a text, in the order of the text, with its kind."""
    return Reader(text, language).read()

"""Reading the numbers in an answer, in Norwegian or English (spec 0003, tasks 1 and 2).

The tokenizer normalises the text to Unicode NFC and finds, in this order, citation
markers and the numbers of ordered list items, dates, periods, the forms that block an
answer, and other numbers. Each pass claims its span of text, so a later pass never
reads a digit that an earlier one read. It uses no model and no network.

A number is read with its value and its scale, ``12,50`` as ``Decimal("12.50")``, and
with any percent, percentage point or scale word after it. Whether a number is a value,
a period, a table ID or a number from a label is decided when it is matched against
the cited results, since only they say which years and IDs they hold.
"""

from __future__ import annotations

import datetime
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

from opencited.model import Frozen
from opencited.values import ExactDecimal

type Language = Literal["no", "en"]
type Kind = Literal["number", "date", "period", "marker", "list number", "blocked"]

# Spaces that group digits in Norwegian text (spec 0003).
GROUP_SPACES = " \u00a0\u202f\u2009"
MINUS = "-\u2212"
RANGE_DASHES = "-\u2013"
ASCII_DIGITS = "0123456789"

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
MONTHS_EN = (
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
)
QUARTER_WORDS_NO = ("første", "andre", "tredje", "fjerde")
QUARTER_WORDS_EN = ("first", "second", "third", "fourth")

PER_CENT_WORDS = ("prosent", "per cent", "percent")
POINT_WORDS = ("prosentpoeng", "percentage points")
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
# Cardinal numbers in words. One followed by a percent or scale word blocks an answer,
# since the gate reads no numbers in words.
NUMBER_WORDS = frozenset(
    {
        "null",
        "en",
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
    }
)
# Words that multiply a number, which the gate cannot check (spec 0003).
MULTIPLYING = re.compile(
    r"\b(?:doble[rts]?|doblet|dobling(?:en)?|halvere[rs]?|halvert|halvering(?:en)?|"
    r"tredoble[rt]?|tredoblet|firedoble[rt]?|firedoblet|"
    r"doubled?|doubles|doubling|halved?|halves|halving|tripled?|tripling|quadrupled?|twice)\b",
    re.IGNORECASE,
)
MARKER = re.compile(r"\[(?:E|C)[1-9][0-9]*\]")
LIST_NUMBER = re.compile(r"(?m)^[ \t]*([0-9]+)[.)](?=[ \t])")


class Number(Frozen):
    """A number found in an answer.

    ``position`` and ``end`` are offsets in the normalised text. ``value`` is the number
    as written, with its scale, before any scale word; ``scale`` is the power of ten of
    a scale word after it. ``unit`` says whether a percent or percentage point word
    follows. A date has ``date``, a period ``period`` as SSB writes it, such as
    ``2023K1``, and a blocked form ``reason``.
    """

    position: int
    end: int
    text: str
    kind: Kind
    value: ExactDecimal | None = None
    scale: int = 0
    unit: Literal["per cent", "percentage points"] | None = None
    date: datetime.date | None = None
    period: str | None = None
    reason: str | None = None


@dataclass
class Claims:
    """The spans that earlier passes have read."""

    spans: list[tuple[int, int]]

    def free(self, start: int, end: int) -> bool:
        return all(end <= first or start >= last for first, last in self.spans)

    def take(self, start: int, end: int) -> None:
        self.spans.append((start, end))


def month_pattern(names: tuple[str, ...] | list[str]) -> str:
    return "(?:" + "|".join(names) + ")"


def date_patterns() -> list[tuple[re.Pattern[str], str]]:
    months = month_pattern([*MONTHS_NO, *MONTHS_EN])
    return [
        (re.compile(r"\b([0-9]{4})-([0-9]{2})-([0-9]{2})\b"), "iso"),
        (re.compile(r"\b([0-9]{1,2})\.([0-9]{1,2})\.([0-9]{4})\b"), "dotted"),
        (re.compile(rf"\b([0-9]{{1,2}})\.? ({months}) ([0-9]{{4}})\b", re.IGNORECASE), "day month"),
        (re.compile(rf"\b({months}) ([0-9]{{1,2}}), ([0-9]{{4}})\b", re.IGNORECASE), "month day"),
    ]


def month_number(name: str) -> int:
    lowered = name.lower()
    names = MONTHS_NO if lowered in MONTHS_NO else MONTHS_EN
    return names.index(lowered) + 1


def read_date(kind: str, groups: tuple[str, ...]) -> date | None:
    try:
        match kind:
            case "iso":
                return date(int(groups[0]), int(groups[1]), int(groups[2]))
            case "dotted":
                return date(int(groups[2]), int(groups[1]), int(groups[0]))
            case "day month":
                return date(int(groups[2]), month_number(groups[1]), int(groups[0]))
            case _:
                return date(int(groups[2]), month_number(groups[0]), int(groups[1]))
    except ValueError:
        return None


def period_patterns() -> list[tuple[re.Pattern[str], str]]:
    months = month_pattern([*MONTHS_NO, *MONTHS_EN])
    quarters = month_pattern([*QUARTER_WORDS_NO, *QUARTER_WORDS_EN])
    return [
        (re.compile(r"\b([0-9]{4})K([1-4])\b"), "code quarter"),
        (re.compile(r"\b([0-9]{4})M(0[1-9]|1[0-2])\b"), "code month"),
        (re.compile(r"\b([1-4])\. kvartal ([0-9]{4})\b", re.IGNORECASE), "number quarter"),
        (
            re.compile(
                rf"\b({quarters}) (?:kvartal|quarter) (?:of )?([0-9]{{4}})\b", re.IGNORECASE
            ),
            "word quarter",
        ),
        (re.compile(r"\bQ([1-4]) ([0-9]{4})\b"), "number quarter"),
        (re.compile(rf"\b({months}) ([0-9]{{4}})\b", re.IGNORECASE), "month"),
    ]


def read_period(kind: str, groups: tuple[str, ...]) -> str:
    match kind:
        case "code quarter":
            return f"{groups[0]}K{groups[1]}"
        case "code month":
            return f"{groups[0]}M{groups[1]}"
        case "number quarter":
            return f"{groups[1]}K{groups[0]}"
        case "word quarter":
            word = groups[0].lower()
            quarter = (QUARTER_WORDS_NO if word in QUARTER_WORDS_NO else QUARTER_WORDS_EN).index(
                word
            )
            return f"{groups[1]}K{quarter + 1}"
        case _:
            return f"{groups[1]}M{month_number(groups[0]):02d}"


class Reader:
    """Reads the numbers of one text in one language."""

    def __init__(self, text: str, language: Language) -> None:
        self.text = unicodedata.normalize("NFC", text)
        self.language: Language = language
        self.claims = Claims([])
        self.found: list[Number] = []

    def add(self, number: Number) -> None:
        self.found.append(number)
        self.claims.take(number.position, number.end)

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
        self.blocking_forms()
        self.numbers()
        return tuple(sorted(self.found, key=lambda number: (number.position, number.end)))

    def markers(self) -> None:
        for match in MARKER.finditer(self.text):
            self.add(Number(position=match.start(), end=match.end(), text=match[0], kind="marker"))
        for match in LIST_NUMBER.finditer(self.text):
            start, end = match.span(1)
            if self.claims.free(start, end + 1):
                self.add(
                    Number(
                        position=start,
                        end=end + 1,
                        text=self.text[start : end + 1],
                        kind="list number",
                    )
                )

    def dates(self) -> None:
        for pattern, kind in date_patterns():
            for match in pattern.finditer(self.text):
                if not self.claims.free(*match.span()):
                    continue
                when = read_date(kind, match.groups())
                if when is None:
                    self.block(*match.span(), "a date that does not exist")
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

    def periods(self) -> None:
        for pattern, kind in period_patterns():
            for match in pattern.finditer(self.text):
                if self.claims.free(*match.span()):
                    period = read_period(kind, match.groups())
                    self.add(
                        Number(
                            position=match.start(),
                            end=match.end(),
                            text=match[0],
                            kind="period",
                            period=period,
                        )
                    )

    def blocking_forms(self) -> None:
        for match in MULTIPLYING.finditer(self.text):
            if self.claims.free(*match.span()):
                self.block(
                    *match.span(), "a word that multiplies a number, which the gate cannot check"
                )
        words = "|".join(re.escape(word) for word in sorted(NUMBER_WORDS, key=len, reverse=True))
        after = "|".join(
            re.escape(word)
            for word in sorted(
                {*PER_CENT_WORDS, *POINT_WORDS, *SCALE_WORDS["no"], *SCALE_WORDS["en"]},
                key=len,
                reverse=True,
            )
        )
        in_words = re.compile(
            rf"\b(?:{words})(?:[ -](?:{words}))*[ \u00a0](?:%|(?:{after})(?![a-zæøå]))",
            re.IGNORECASE,
        )
        for match in in_words.finditer(self.text):
            if self.claims.free(*match.span()):
                self.block(*match.span(), "a number written in words with a percent or scale word")
        for index, char in enumerate(self.text):
            if (
                char not in ASCII_DIGITS
                and unicodedata.category(char) in ("Nd", "No")
                and self.claims.free(index, index + 1)
            ):
                self.block(index, index + 1, "a digit other than 0 to 9")
        if self.language == "no":
            for match in re.finditer(r"\bbillion(?:er)?\b", self.text, re.IGNORECASE):
                if self.claims.free(*match.span()):
                    self.block(
                        *match.span(), "'billion' in Norwegian text, which can mean 10^9 or 10^12"
                    )

    def numbers(self) -> None:
        index = 0
        while index < len(self.text):
            char = self.text[index]
            if char in ASCII_DIGITS and self.claims.free(index, index + 1):
                index = self.number_at(index)
            else:
                index += 1

    def number_at(self, start: int) -> int:
        """Read the number that starts at ``start`` and return the index after it."""
        text = self.text
        if start > 0 and text[start - 1].isalpha():
            end = self.word_end(start)
            self.block(start, end, "a number joined to letters")
            return end
        if start > 0 and text[start - 1] in ".," and (start == 1 or not text[start - 2].isalnum()):
            end = self.run(start)
            self.block(start - 1, end, "a decimal separator without a digit before it")
            return end
        begin = start
        negative = False
        if (
            start > 0
            and text[start - 1] in MINUS
            and (start == 1 or text[start - 2].isspace() or text[start - 2] == "(")
        ):
            begin = start - 1
            negative = True
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

    def word_end(self, start: int) -> int:
        """Return the end of the letters and digits that start at ``start``."""
        end = start
        while end < len(self.text) and (self.text[end].isalnum()):
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
                    self.run(end + 1),
                )
            if end < size and text[end] in GROUP_SPACES and digit(end + 1):
                stop = self.run(end + 1)
                if len(digits) <= 3 and stop - (end + 1) == 3:
                    return Blocked("digits grouped by spaces in English text", stop)
        if end < size and text[end] == point and digit(end + 1):
            stop = self.run(end + 1)
            digits = f"{digits}.{text[end + 1 : stop]}"
            end = stop
            if end < size and text[end] in ".," and digit(end + 1):
                return Blocked("a mix of separators that fits neither language", self.run(end + 1))
        if self.language == "no" and end < size and text[end] == "." and digit(end + 1):
            return Blocked(
                "a point between digits in Norwegian text outside a date", self.run(end + 1)
            )
        return Read(digits, end)

    def trailing(self, end: int) -> After | Blocked:
        """Read the percent, percentage point or scale word after a number, if any."""
        rest = self.text[end:]
        exponent = re.match(r"[eE][+\u2212-]?[0-9]+", rest) or re.match(
            r"[ \u00a0]?[\u00d7x*\u00b7][ \u00a0]?10(?:\^[+\u2212-]?[0-9]+|[\u2070-\u2079\u00b9\u00b2\u00b3]+)",
            rest,
        )
        if exponent:
            return Blocked("exponent notation", end + exponent.end())
        known = {*PER_CENT_WORDS, *POINT_WORDS, *SCALE_WORDS[self.language]}
        if rest[:1].isalpha():
            glued = re.match(r"[^\W\d_]+\.?", rest)
            word = glued[0].lower() if glued else ""
            if word not in known and word.rstrip(".") not in known:
                return Blocked("a number joined to letters", self.word_end(end))
        space = re.match(r"[ \u00a0\u202f\u2009]?", rest)
        offset = space.end() if space else 0
        after = rest[offset:]
        if after.startswith("%"):
            return After("per cent", 0, end + offset + 1)
        lowered = after.lower()

        def ends_word(word: str) -> bool:
            return lowered.startswith(word) and (
                word.endswith(".") or not lowered[len(word) : len(word) + 1].isalpha()
            )

        for word in POINT_WORDS:
            if ends_word(word):
                return After("percentage points", 0, end + offset + len(word))
        for word in PER_CENT_WORDS:
            if ends_word(word):
                return After("per cent", 0, end + offset + len(word))
        for word, power in sorted(
            SCALE_WORDS[self.language].items(), key=lambda item: -len(item[0])
        ):
            if ends_word(word):
                return After(None, power, end + offset + len(word))
        return After(None, 0, end)


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
    unit: Literal["per cent", "percentage points"] | None
    scale: int
    end: int


def read_numbers(text: str, language: Language) -> tuple[Number, ...]:
    """Return every number in a text, in the order of the text, with its kind."""
    return Reader(text, language).read()

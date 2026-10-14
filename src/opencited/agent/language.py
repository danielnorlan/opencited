"""The language of a question: Norwegian or English (spec 0004).

The answer uses the language of the question unless the caller sets one. The rule is
plain and gives the same result each time: the language with more of its common words
in the question wins, every word with æ, ø or å counts as one Norwegian word, and a
tie goes to Norwegian, since the sources are Norwegian. An English question that names
more places with æ, ø or å than it has common English words is read as Norwegian;
`--language` sets the language for such a question.
"""

from __future__ import annotations

import re

from opencited.sources import Language

# Common words of questions about statistics. No word is in both lists.
NORWEGIAN = frozenset(
    [
        "hvor",
        "hva",
        "hvem",
        "hvilke",
        "hvilken",
        "hvilket",
        "hvordan",
        "hvorfor",
        "mange",
        "mye",
        "er",
        "var",
        "ble",
        "blir",
        "og",
        "eller",
        "av",
        "til",
        "fra",
        "som",
        "det",
        "den",
        "de",
        "et",
        "med",
        "uten",
        "om",
        "ved",
        "etter",
        "siden",
        "mellom",
        "antall",
        "andel",
        "befolkning",
        "befolkningen",
        "innbyggere",
        "folketall",
        "kommune",
        "kommuner",
        "kommunen",
        "fylke",
        "fylker",
        "norge",
        "landet",
        "prosent",
        "endring",
        "gjennomsnitt",
        "totalt",
        "sammenlignet",
        "i",
        "vekst",
        "ledighet",
        "arbeidsledighet",
        "sysselsatte",
        "inntekt",
        "pris",
        "priser",
        "innvandrere",
        "husholdninger",
        "bor",
        "bodde",
        "fikk",
    ]
)
ENGLISH = frozenset(
    [
        "how",
        "what",
        "who",
        "which",
        "why",
        "many",
        "much",
        "is",
        "are",
        "was",
        "were",
        "did",
        "does",
        "do",
        "and",
        "or",
        "of",
        "to",
        "from",
        "that",
        "the",
        "with",
        "without",
        "about",
        "after",
        "since",
        "between",
        "number",
        "share",
        "population",
        "people",
        "inhabitants",
        "municipality",
        "municipalities",
        "county",
        "counties",
        "norway",
        "country",
        "percent",
        "percentage",
        "change",
        "average",
        "total",
        "compared",
        "in",
        "rate",
        "growth",
        "unemployment",
        "employed",
        "employment",
        "income",
        "price",
        "prices",
        "immigrants",
        "households",
        "live",
        "lived",
        "births",
        "deaths",
    ]
)
WORD = re.compile(r"[^\W\d_]+")


def language_of(question: str) -> Language:
    """Return the language of a question."""
    words = WORD.findall(question.lower())
    norwegian = sum(word in NORWEGIAN or any(letter in word for letter in "æøå") for word in words)
    english = sum(word in ENGLISH for word in words)
    return "en" if english > norwegian else "no"


__all__ = ["language_of"]

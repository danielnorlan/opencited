"""The system prompt, its version, the retry message and the model's refusal line."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from answering import evidences

from opencited.agent import prompt
from opencited.agent.prompt import (
    PROMPT_VERSION,
    ModelRefusal,
    refusal_in,
    retry_message,
    system_prompt,
)
from opencited.gate import check
from opencited.snapshots import SnapshotStore
from opencited.sources import Language
from opencited.values import format_number

# The SHA-256 of the prompt texts for each version. A change to the texts without a
# new version fails here, since every answer records the version it was made with. The
# texts include rendered retry messages, so that the parts of a block hint and the
# gate's report that the model reads count too.
TEXTS = {
    "1": "0cbf90b1b19623329bbf09b56bd3223ad1cdcb9b31a98afd6b8c932b8411fea2",
    "2": "fa4ea61be2406c0ade156a6d602532dbd5cb00c668d5e9420b73143461639ae3",
    "3": "bd2e50b1ca5f176e535cd4d35c0172c4037f629a7b04482ac5ef779b46a7a29c",
    "4": "a8363ff1724fddd369a4b97b469fe7869b9e41aab8941f96e112762ad04fdb12",
    "5": "66a5ab9223ea05c878bcd3f6b449af974e99a420b00e897b5d626680379d8a49",
    "6": "e9cbea8be7e011cd9eeaab1e9bba3f06901b027341ec707d41cff4ffc4781f63",
    "7": "f13da027e56d180c8713b4156917310aa64bad14e7037790e072d01e56766d2a",
    "8": "d418cd01c57b1bff1ababdf7e9e3a5adea56e3c1a2b8cde1a7bbe7d043e7d30b",
    "9": "268d269508e21392401b61bd58155701d2c50cc8710e4021ca42f2762b9e0582",
}


# Answers whose numbers all stand in blocks that cite nothing, so that their retry
# messages need no evidence: every kind of block, a cut start, one to four numbers, a
# date, a day, a period, a value and a number on a code fence's own line.
RENDERED: tuple[tuple[str, Language], ...] = (
    (
        "The premise is contradicted by the figures: the population of Norway did not fall "
        "from 2025 to 2026.",
        "en",
    ),
    ("- In 2026 it was larger.", "en"),
    ("| Country | Population, 1 January 2026 |", "en"),
    ("# Figures for 2019M05", "en"),
    ("Fra 2015\ntil 2025 og 2026 vokste den per 1. januar.", "no"),
    ("~~~ 99% growth\nx\n~~~", "en"),
)


def rendered() -> str:
    return "".join(retry_message(check(text, language, []), text) for text, language in RENDERED)


def test_a_change_to_the_prompt_texts_needs_a_new_version() -> None:
    texts = "".join(
        [
            prompt.SYSTEM_PROMPT,
            prompt.RETRY,
            prompt.UNCITED,
            repr(prompt.LANGUAGES),
            prompt.REFUSAL.pattern,
            repr(prompt.HINTS),
            repr(prompt.SPENT_NOTES),
            prompt.UNCITED_BLOCK,
            str(prompt.QUOTED),
            rendered(),
        ]
    )

    assert hashlib.sha256(texts.encode()).hexdigest() == TEXTS[PROMPT_VERSION]


def test_the_prompt_names_the_language_of_the_answer_and_nothing_else_differs() -> None:
    norwegian, english = system_prompt("no"), system_prompt("en")

    assert "Write the answer in Norwegian (bokmål)." in norwegian
    assert "Write the answer in English." in english
    assert norwegian.replace("Norwegian (bokmål)", "English") == english


def test_the_prompt_keeps_numbers_from_a_false_premise_out_of_answers() -> None:
    text = system_prompt("en")

    assert "Do not repeat a number from the question that no result holds" in text


def test_the_prompt_teaches_the_citation_markers_and_the_refusal_lines() -> None:
    text = system_prompt("en")

    for part in ("[E1]", '"REFUSE: no data"', '"REFUSE: outside the sources"', "calculate"):
        assert part in text


def test_a_retry_sends_the_gate_report_back(tmp_path: Path) -> None:
    found = evidences(SnapshotStore(tmp_path))
    text = "I 2026 bodde det 5,7 millioner personer i Norge [E1]."
    report = check(text, "no", found.evidence)

    message = retry_message(report, text)

    assert message.startswith("The evidence gate blocked your answer.\n")
    assert report.text() in message
    assert "or leave the number out.\n" in message
    # The failed value gets what to do about it, once.
    assert message.endswith(
        "Write '5,7 millioner' as a result gives it, with its sign and its per cent word, "
        "or leave it out."
    )


def test_a_retry_after_a_failed_date_says_to_cite_the_result_that_gives_it(
    tmp_path: Path,
) -> None:
    found = evidences(SnapshotStore(tmp_path))
    text = "Per 1. januar 2026 og 1. januar 2026 bodde det mange i Norge [E1]."
    report = check(text, "no", found.evidence)

    message = retry_message(report, text)

    hint = (
        "Cite the source result that gives '1. januar 2026' in the same paragraph, list item "
        "or table row, or leave the date out."
    )
    assert message.count(hint) == 1


def test_a_retry_after_a_year_that_no_cited_result_holds_says_to_cite_its_source(
    tmp_path: Path,
) -> None:
    # C1 read E1, which selected 2026 and not 2015, so only 2015 fails (spec 0003,
    # Numbers that are not values, item 6).
    found = evidences(SnapshotStore(tmp_path))
    growth = format_number(found.step("growth"), "no")
    text = f"Folketallet økte med {growth} prosent fra 2015 til 2026 [C1]."
    report = check(text, "no", found.evidence)

    message = retry_message(report, text)

    assert not report.passed
    assert message.count(year_hint("2015")) == 1
    assert year_hint("2026") not in message
    assert "per cent word" not in message


def test_a_four_digit_count_that_matches_nothing_gets_the_same_hint(tmp_path: Path) -> None:
    # The gate cannot tell a year from a count, so the hint covers both.
    found = evidences(SnapshotStore(tmp_path))
    text = "Det bodde 5432 personer i Norge [E1]."
    report = check(text, "no", found.evidence)

    assert retry_message(report, text).endswith(year_hint("5432"))


def test_a_retry_after_a_failed_period_says_to_cite_the_result_that_selected_it(
    tmp_path: Path,
) -> None:
    found = evidences(SnapshotStore(tmp_path))
    text = "I 2019M05 bodde det mange i Norge [E1]."
    report = check(text, "no", found.evidence)

    assert retry_message(report, text).endswith(
        "Cite the source result that selected '2019M05' in the same paragraph, list item "
        "or table row, or leave the period out."
    )


def test_a_number_in_a_block_that_cites_nothing_gets_a_hint_that_quotes_the_block(
    tmp_path: Path,
) -> None:
    found = evidences(SnapshotStore(tmp_path))
    text = "Påstanden stemmer ikke for 2025.\n\nDet er alt [E1]."
    report = check(text, "no", found.evidence)

    message = retry_message(report, text)

    assert not report.passed
    assert message.endswith(
        "The paragraph that begins 'Påstanden stemmer ikke for 2025.' cites no result. "
        "Cite in it the result that gives '2025', or leave it out."
    )
    assert "per cent word" not in message
    assert year_hint("2025") not in message


def test_a_table_row_that_cites_nothing_is_named_and_quoted(tmp_path: Path) -> None:
    # The form of a table in an answer of the published run of 2026-10-18 (#73): its
    # header row named a date and cited nothing, and the model kept it so after the
    # hint for a date.
    found = evidences(SnapshotStore(tmp_path))
    value = format_number(found.value("2026"), "en")
    text = (
        f"In 2026, {value} people lived in Norway [E1].\n\n"
        "| Country | Population, 1 January 2026 |\n"
        "| --- | --- |\n"
        f"| Norway | {value} [E1] |"
    )
    report = check(text, "en", found.evidence)

    message = retry_message(report, text)

    assert not report.passed
    assert message.endswith(
        "The table row that begins '| Country | Population, 1 January 2026 |' cites no "
        "result. Cite in it the result that gives '1 January 2026', or leave it out."
    )
    assert "or leave the date out" not in message


def test_the_failed_numbers_of_a_block_get_one_hint_and_a_long_block_is_cut(
    tmp_path: Path,
) -> None:
    found = evidences(SnapshotStore(tmp_path))
    text = (
        "The premise is contradicted by the figures: the population of Norway did not fall "
        "from 2025 to 2026.\n\n- In 2026 it was larger.\n\nSee the figures [C1]."
    )
    report = check(text, "en", found.evidence)

    message = retry_message(report, text)

    assert message.endswith(
        "The paragraph that begins 'The premise is contradicted by the figures: the "
        "population...' "
        "cites no result. Cite in it the result that gives '2025' and '2026', or leave them "
        "out.\nThe list item that begins '- In 2026 it was larger.' cites no result. Cite "
        "in it the result that gives '2026', or leave it out."
    )


def test_a_number_written_twice_in_a_block_is_named_once(tmp_path: Path) -> None:
    found = evidences(SnapshotStore(tmp_path))
    text = "Per 1. januar 2026 og 1. januar 2026 bodde det mange.\n\nDet er alt [E1]."
    report = check(text, "no", found.evidence)

    message = retry_message(report, text)

    assert message.endswith(
        "The paragraph that begins 'Per 1. januar 2026 og 1. januar 2026 bodde det mange.' "
        "cites no result. Cite in it the result that gives '1. januar 2026', or leave it out."
    )


@pytest.mark.parametrize(
    "other",
    ["Kort.", "Noe helt annet, som er mye lengre enn den teksten som ble sjekket.\n\nMer."],
)
def test_a_text_that_is_not_the_checked_one_gets_the_hint_without_a_quote(
    tmp_path: Path, other: str
) -> None:
    found = evidences(SnapshotStore(tmp_path))
    report = check("Påstanden stemmer ikke for 2025.\n\nDet er alt [E1].", "no", found.evidence)

    message = retry_message(report, other)

    assert message.endswith(
        "Cite the result that gives '2025' in the same paragraph, list item or table row, "
        "or leave it out."
    )


def test_a_number_in_no_block_gets_the_hint_to_move_it_or_leave_it_out(
    tmp_path: Path,
) -> None:
    found = evidences(SnapshotStore(tmp_path))
    text = "~~~ 2025 growth\nx\n~~~\nDet er alt [E1]."
    report = check(text, "no", found.evidence)

    assert retry_message(report, text).endswith(
        "'2025' stands outside every paragraph, list item and table row, such as on a code "
        "fence's own line, where no citation counts. Move it into a paragraph that cites "
        "its result, or leave it out."
    )


@pytest.mark.parametrize(
    ("text", "hint"),
    [
        # A number at a block's first character.
        (
            "2025 var lavere.\n\nDet er alt [E1].",
            "The paragraph that begins '2025 var lavere.' cites no result. Cite in it the "
            "result that gives '2025', or leave it out.",
        ),
        # A period, whose reason in such a block is its own.
        (
            "I 2019M05 bodde det mange.\n\nDet er alt [E1].",
            "The paragraph that begins 'I 2019M05 bodde det mange.' cites no result. Cite in "
            "it the result that gives '2019M05', or leave it out.",
        ),
        # Three numbers, and a paragraph of two lines whose spacing is collapsed.
        (
            "Fra 2015\ntil 2025 og 2026 vokste den.\n\nDet er alt [E1].",
            "The paragraph that begins 'Fra 2015 til 2025 og 2026 vokste den.' cites no "
            "result. Cite in it the result that gives '2015', '2025' and '2026', or leave "
            "them out.",
        ),
        # A start of 61 characters is cut at a word, and one of 60 is quoted whole.
        (
            "Folketallet i 2025 var omtrent det samme som i de andre årene\n\nAnnet [E1].",
            "The paragraph that begins 'Folketallet i 2025 var omtrent det samme som i de "
            "andre...' cites no result. Cite in it the result that gives '2025', or leave it "
            "out.",
        ),
        (
            "Folketallet i 2025 var omtrent det samme som i de andre åra.\n\nAnnet [E1].",
            "The paragraph that begins 'Folketallet i 2025 var omtrent det samme som i de "
            "andre åra.' cites no result. Cite in it the result that gives '2025', or leave "
            "it out.",
        ),
    ],
)
def test_the_block_hint_quotes_and_lists_as_it_should(tmp_path: Path, text: str, hint: str) -> None:
    found = evidences(SnapshotStore(tmp_path))
    report = check(text, "no", found.evidence)

    assert retry_message(report, text).endswith(hint)


def year_hint(text: str) -> str:
    return (
        f"If '{text}' is a year, cite the source result that selected it in the same "
        "paragraph, list item or table row; otherwise write it as a result gives it, or "
        "leave it out."
    )


def test_a_retry_for_an_uncited_answer_has_no_hints(tmp_path: Path) -> None:
    found = evidences(SnapshotStore(tmp_path))
    report = check("Ingen tall her.", "no", found.evidence)

    assert retry_message(report, "Ingen tall her.") == prompt.RETRY.format(report=report.text())


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("REFUSE: no data", ModelRefusal("no data", "")),
        (
            "REFUSE: no data\nStatistikkbanken har ingen tabell over dette.\n",
            ModelRefusal("no data", "Statistikkbanken har ingen tabell over dette."),
        ),
        (
            "\n  REFUSE: outside the sources  \nNot statistics.",
            ModelRefusal("outside the sources", "Not statistics."),
        ),
        ("Jeg fant ingenting.\nREFUSE: no data", ModelRefusal("no data", "Jeg fant ingenting.")),
        ("REFUSE: no data.", ModelRefusal("no data", "")),
        ("**REFUSE: no data**", ModelRefusal("no data", "")),
        ("> `refuse: Outside the sources`", ModelRefusal("outside the sources", "")),
    ],
)
def test_a_reply_with_a_refusal_line_is_a_refusal(text: str, expected: ModelRefusal) -> None:
    assert refusal_in(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "REFUSE: no data at all",
        "REFUSE: outside",
        "I REFUSE: no data",
    ],
)
def test_any_other_reply_is_an_answer(text: str) -> None:
    assert refusal_in(text) is None


def test_an_answer_that_cites_nothing_is_asked_to_cite_or_refuse() -> None:
    message = prompt.uncited_message()

    assert message.startswith("Your answer cites no result.")
    assert "[E1]" in message
    assert "refuse" in message

# Spec 0003: Evidence gate

| Field | Value |
| --- | --- |
| Status | Approved |
| Milestone | M2 |
| Owner | Daniel Norlan |
| Created | 2026-10-13 |

## Goal

Before an answer is released, every number in it is matched to a value in the
results it cites. An answer with a number that does not match is blocked, and the
gate says which number failed and why.

## Background

- [ADR 0004](../adr/0004-evidence-gate.md) decides that a deterministic gate checks
  every number, and notes that number formats need explicit rules.
- Tests with Babel 2.18.0 on 2026-10-13 showed that its `parse_decimal` does not fit
  this use. For Norwegian it rejects digits grouped by an ordinary space or by
  U+202F in strict mode, and the minus sign U+2212 in both modes. In lenient mode it
  reads `12,5` as 125 in English. None of the libraries found was confirmed to read
  Norwegian number formats into decimals, so the gate has its own tokenizer.
- The agent ([spec 0004](0004-agent.md)) gives every tool result an evidence ID, and
  the answer cites results by these IDs.

## Users

- The agent, before it releases an answer.
- The scorer of the evaluation suite ([spec 0005](0005-evaluation-suite.md)), which
  checks answers again with the same rules.

## Requirements

### Input

1. The answer text in Markdown, its language (`no` or `en`) and the evidence of the
   run: every source result and every calculation result, each with its evidence
   ID.
2. Citation markers have the form `[E1]` for a source result and `[C1]` for a
   calculation result.
3. A block is a paragraph, a list item or a table row of the answer. A result is
   cited in a block when the block holds its marker.

### Finding numbers

1. The gate normalises the text to Unicode NFC and finds numbers with a deterministic
   tokenizer. It uses no model and makes no network calls. It reads dates first,
   then periods and ranges, then other numbers.
2. In Norwegian text, digits are grouped in threes by a space, U+00A0, U+202F or
   U+2009, the decimal separator is a comma, and the minus sign is `-` or U+2212.
3. In English text, digits are grouped in threes by a comma, the decimal separator is
   a point, and the minus sign is `-` or U+2212.
4. A hyphen or an en dash between two numbers without spaces marks a range, and each
   end is read on its own. A hyphen is a minus sign only at the start of a number,
   after a space or after an opening bracket.
5. A number can carry a percent sign or word (`prosent`, `per cent`, `percent`), a
   percentage point word (`prosentpoeng`, `percentage points`) or a scale word:
   `tusen`, `mill.`, `million`, `millioner`, `mrd.`, `milliard` or `milliarder` in
   Norwegian, and `thousand`, `million` or `billion` in English.
6. These forms block the answer: exponent notation, digits other than 0 to 9, a mix
   of separators that fits neither language, a point between digits in Norwegian
   text outside a date, digits grouped by spaces in English text, `billion` in
   Norwegian text, which can mean 10^9 or 10^12, a number written in words followed
   by a percent or scale word, and words that multiply, such as `doblet`, `halvert`,
   `doubled` and `halved`.

### Numbers that are not values

1. A period passes when a source result cited in the block selected it. Periods are
   years, SSB's codes such as `2023K1` and `2023M01`, and quarters and months in
   words in either language, such as `1. kvartal 2023` or `January 2023`.
2. A date passes when it equals the last update or the retrieval date of a source
   result cited in the block. Dates are written as `2026-10-13`, as `13.10.2026` or
   with the month in words in either language.
3. A five-digit table ID passes when it is the ID of a source result cited in the
   block.
4. A number that appears in the title of a source result cited in the block, or in
   the label of a value that result selected, passes when it is written without a
   unit, percent or scale word.
5. Citation markers and the numbers of ordered list items are ignored.

### Matching values

1. Every other number is a value. A value must be in a block that cites at least
   one result.
2. A value matches when a result cited in the block holds a value `v` that, rounded
   with ties away from zero to the decimals written, equals the written number. For
   a calculation result, `v` is the exact result before rounding. For a number with
   a scale word, the gate compares the written number with `v` divided by the
   scale.
3. A number with a percent sign or word matches only values whose unit is per cent
   and the results of `change_percent` and `share_percent`. A number with a
   percentage point word matches only the difference between two per cent values.
   Other numbers match only values that are not per cent.
4. The sign must match, so a negative value is written with a minus sign.
5. No tolerance applies beyond this rounding rule.

### Report

1. The report lists every number with its position in the text, its kind, the value
   read, and either the evidence it matched or the reason it failed. The evidence is
   an evidence ID with the cell or the calculation step.
2. The answer passes when no number failed.
3. The report is part of the answer, and the agent sends it back to the model when
   it asks for a corrected answer.

## Verification

1. Unit tests for every format, kind and rule, in both languages, with evidence built
   from the recorded fixtures of spec 0001.
2. Property-based tests: a decimal formatted in Norwegian or English by the
   project's formatter and read back by the gate keeps its value and its scale.
3. A corpus of example answers over recorded fixture data, each with the decision
   that the gate must reach and the rule it tests.
4. A test for every form that blocks an answer.

## Acceptance criteria

- [ ] Every format above is read with the right value and scale in Norwegian and in
      English, and every blocking form blocks the answer.
- [ ] A value that does not match the cited results after rounding blocks the
      answer, including a wrong sign, a wrong scale word, a percentage written
      without a percent word and a value in a block without a citation.
- [ ] Periods, dates, table IDs and numbers from labels pass only when the cited
      results contain them.
- [ ] The report lists every number with its match or the reason it failed.
- [ ] A decimal formatted by the project's formatter and read back keeps its value
      and scale, in property-based tests.
- [ ] The gate gives the same report for the same input and makes no network calls.

## Out of scope

- Claims in words, such as "the highest" or "rose". A change is written as a signed
  number.
- Numbers written in words, apart from the blocking cases above.
- Checking what a number from a label or a title refers to. Such a number passes
  when its block cites the result, and the evaluation suite shows whether this
  matters.
- Languages other than Norwegian Bokmål and English.

## Tasks

1. Tokenizer and number formats for both languages, and the formatter.
2. Kinds: periods, dates, table IDs, numbers from labels and markers.
3. Matching rules and the report.
4. Property-based tests and the corpus of example answers.

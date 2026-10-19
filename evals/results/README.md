# Published runs

Each JSON file here is the report of one published run of the evaluation suite, which
`evals/report.py` wrote from the run's Inspect log. A report names the question set,
the commit, the model and the endpoint, the settings and prices, and the result of
every question ([spec 0005](../../docs/specs/0005-evaluation-suite.md)).

| Report | Question set | Model | Correct |
| --- | --- | --- | --- |
| [2026-10-14T1751-v1-deepseek-deepseek-flash.json](2026-10-14T1751-v1-deepseek-deepseek-flash.json) | v1 | `deepseek-flash` | 27 of 44 |
| [2026-10-18T0807-v1-deepseek-deepseek-flash.json](2026-10-18T0807-v1-deepseek-deepseek-flash.json) | v1 | `deepseek-flash` | 40 of 44 |
| [2026-10-18T0815-v2-deepseek-deepseek-flash.json](2026-10-18T0815-v2-deepseek-deepseek-flash.json) | v2 | `deepseek-flash` | 33 of 43 |
| [2026-10-18T1119-v1-deepseek-deepseek-flash.json](2026-10-18T1119-v1-deepseek-deepseek-flash.json) | v1 | `deepseek-flash` | 40 of 44 |
| [2026-10-18T1127-v3-deepseek-deepseek-flash.json](2026-10-18T1127-v3-deepseek-deepseek-flash.json) | v3 | `deepseek-flash` | 40 of 43 |
| [2026-10-18T1342-v1-deepseek-deepseek-flash.json](2026-10-18T1342-v1-deepseek-deepseek-flash.json) | v1 | `deepseek-flash` | 42 of 44 |
| [2026-10-18T1350-v3-deepseek-deepseek-flash.json](2026-10-18T1350-v3-deepseek-deepseek-flash.json) | v3 | `deepseek-flash` | 40 of 43 |
| [2026-10-18T1440-v1-deepseek-deepseek-flash.json](2026-10-18T1440-v1-deepseek-deepseek-flash.json) | v1 | `deepseek-flash` | 40 of 44 |
| [2026-10-18T1447-v3-deepseek-deepseek-flash.json](2026-10-18T1447-v3-deepseek-deepseek-flash.json) | v3 | `deepseek-flash` | 40 of 43 |
| [2026-10-19T1320-v1-deepseek-deepseek-flash.json](2026-10-19T1320-v1-deepseek-deepseek-flash.json) | v1 | `deepseek-flash` | 38 of 44 |
| [2026-10-19T1328-v3-deepseek-deepseek-flash.json](2026-10-19T1328-v3-deepseek-deepseek-flash.json) | v3 | `deepseek-flash` | 39 of 43 |

## 2026-10-19, question sets v1 and v3 from release 0.1.0, `deepseek-flash`

Both runs started from commit `ec5b246`, tagged `v0.1.0`, on 2026-10-19, set v1 at
13:20 UTC and set v3 at 13:28 UTC, with the model, settings, prices and limits of the
earlier runs. Since the runs from `5d737f2`, a refusal line with its reason in
Norwegian counts as a refusal (#84) and weeks count as periods in the gate (#86), in
version 11 of the prompt texts.

| | v1, `5d737f2` | v1, `ec5b246` | v3, `5d737f2` | v3, `ec5b246` |
| --- | --- | --- | --- | --- |
| Correct | 40 of 44 | 38 of 44 | 40 of 43 | 39 of 43 |
| Released answers counted correct | 29 of 30 | 26 of 26 | 28 of 29 | 27 of 29 |
| Answers to questions that should be refused | 1 of 12 | 0 of 12 | 0 of 12 | 0 of 12 |
| Answerable questions refused | 3 | 6 | 2 | 2 |
| Refusals after three blocked attempts | 0 | 0 | 0 | 0 |
| First attempts that the gate blocked | 17 of 30 | 12 of 26 | 14 of 29 | 15 of 29 |
| Retries | 20 | 12 | 15 | 17 |
| Tokens | 1,715,899 | 1,727,525 | 1,393,664 | 1,372,405 |

`out-of-scope-stockholm` (v1), which the run from `5d737f2` released as an answer
because its reply opened with the refusal line in Norwegian, was refused with the
same line, "REFUSE: utenfor kildene".

Set v1 had 38 of 44 correct: 17 of 22 in Norwegian and 21 of 22 in English. Lookup had
6 of 7, change 5 of 5, share 2 of 5, sum 4 of 5, ranking 4 of 5, false premise 5 of 5,
unanswerable 5 of 5 and out of scope 7 of 7. All 26 released answers were correct.
The six wrong verdicts are refusals at the token limit of 100,000 tokens, all in
questions about the population. Three of them, `share-children-norway-2025`,
`share-net-migration-norway-2025` and `ranking-largest-age-group-2026`, were correct in
the v1 run from `5d737f2`, and the other three reached the limit there as well.

- `lookup-population-norway-2026` reached 104,929 tokens after four searches, the
  metadata of tables 05277 and 11342 and a query of 11342, and
  `ranking-largest-age-group-2026` reached 104,200 after five searches, two queries of
  table 07459 by single years of age and a calculation. The last reply of each held
  the answer that the v1 run at 13:42 on 2026-10-18 released and that was scored
  correct, "5 627 400 personer" and "16–44 år" with 2 142 123 persons. The agent does
  not check a reply once a limit is reached (spec 0004).
- `share-80-plus-norway-2026` reached 117,794 tokens after six searches and a query of
  table 11805, and `sum-67-plus-norway-2026` reached 117,501 after eight searches.
- `share-children-norway-2025` reached 112,253 tokens after three queries of table
  10211, one of them for every age, and two calculations; its last reply said that it
  still needed the total population. `share-net-migration-norway-2025` reached 104,441
  after eight searches and queries of tables 05395 and 10516.

Set v3 had 39 of 43 correct: 22 of 23 in Norwegian and 17 of 20 in English. Lookup had
7 of 7, change 5 of 6, share 5 of 5, sum 5 of 5, ranking 3 of 4, false premise 2 of 4,
unanswerable 8 of 8 and out of scope 4 of 4.

- `change-offences-2015-2025` reached the token limit with 101,339 tokens, and
  `false-premise-offences-doubled` refused with "no data" after 12 model calls. Each
  queried table 08484 four times with the offence code `0-999`, whose cells for 2015
  and 2025 are ".". The code `1AAAAA-9ZZZZz` holds the totals that the reference plans
  read, 351,459 and 348,508.
- `ranking-age-group-highest-wage-2025` searched once and read table 11421, whose
  five-year bands differ from the groups that the question names, and answered with
  the band "55-59 years", saying that the table does not hold those groups. The run
  from `5d737f2` read table 13312, which holds them.
- `false-premise-women-earn-more-gap-2025` gave the gap as women's wage minus men's,
  "−6 870 kr per måned", as in the earlier runs.

The two runs used 1,727,525 and 1,372,405 tokens, and the largest correct answer used
57,814. With every input token counted at the price without a cache hit, the agent's
estimates of the cost are $0.5389 and $0.4345. The account's balance at DeepSeek fell
from $3.83 to $3.70 over both runs.

## 2026-10-18, question sets v1 and v3 with prompt texts v10, `deepseek-flash`

Both runs started from commit `5d737f2` on 2026-10-18, set v1 at 14:40 UTC and set v3
at 14:47 UTC, with the model, settings, prices and limits of the earlier runs. Since
the runs from `ffb297f`, rule 10 of the system prompt asks for a table that holds the
groups that a question names, or for a sum of the counts of smaller groups, and never
for a value joined from the averages, medians, rates or shares of smaller groups
(#80), in version 10 of the prompt texts.

| | v1, 13:42 | v1, 14:40 | v3, 13:50 | v3, 14:47 |
| --- | --- | --- | --- | --- |
| Correct | 42 of 44 | 40 of 44 | 40 of 43 | 40 of 43 |
| Released answers counted correct | 30 of 30 | 29 of 30 | 28 of 31 | 28 of 29 |
| Answers to questions that should be refused | 0 of 12 | 1 of 12 | 0 of 12 | 0 of 12 |
| Answerable questions refused | 2 | 3 | 0 | 2 |
| Refusals after three blocked attempts | 0 | 0 | 0 | 0 |
| First attempts that the gate blocked | 17 of 30 | 17 of 30 | 15 of 31 | 14 of 29 |
| Retries | 20 | 20 | 16 | 15 |
| Tokens | 1,690,484 | 1,715,899 | 1,237,735 | 1,393,664 |

`ranking-age-group-highest-wage-2025` (v3), which at 13:50 joined the five-year bands
of table 11421 with an unweighted mean, read table 13312 and named its group "55-66
years"; the answer says that the table "holds exactly these five age groups".
`lookup-weddings-2025` read table 09659 again and was correct.

Set v1 had 40 of 44 correct: 18 of 22 in Norwegian and 22 of 22 in English. Lookup had
6 of 7, change 5 of 5, share 4 of 5, sum 4 of 5, ranking 5 of 5, false premise 5 of 5,
unanswerable 5 of 5 and out of scope 6 of 7.

- `out-of-scope-stockholm` asks for the population of Stockholm. The reply opened
  with "REFUSE: utenfor kildene", the refusal line in Norwegian, which the agent reads
  only in English, so the reply went to the gate as an answer. It held no number, and
  once it cited the searches it passed and was released as an answer to a question
  that should be refused.
- `lookup-population-norway-2026` reached the token limit with 104,577 tokens, after
  four searches and the metadata of two tables. Its last reply held a correct
  answer, "1. januar 2026 bodde det 5 627 400 personer i Norge", which the agent does
  not check once a limit is reached (spec 0004).
- `share-80-plus-norway-2026` and `sum-67-plus-norway-2026` reached the token limit as
  before, in the middle of their queries.

Set v3 had 40 of 43 correct: 22 of 23 in Norwegian and 18 of 20 in English. Lookup had
6 of 7, change 5 of 6, share 5 of 5, sum 5 of 5, ranking 4 of 4, false premise 3 of 4,
unanswerable 8 of 8 and out of scope 4 of 4.

- `lookup-offences-2025` stopped at the limit of 12 model calls after repeated queries
  of tables 08485 and 08484 with the offence code `0-999`, and
  `change-offences-2015-2025` reached the token limit after querying table 08484 with
  the same code, whose cell for 2025 is ".". At 11:27 and 13:50 both found the code
  that holds the total.
- `false-premise-women-earn-more-gap-2025` gave the gap as women's wage minus men's,
  "−6 870 kr per måned for kvinnene", as before.

The two runs used 1,715,899 and 1,393,664 tokens, and the largest correct answer used
86,622. With every input token counted at the price without a cache hit, the agent's
estimates of the cost are $0.5373 and $0.4413. The account's balance at DeepSeek fell
from $4.01 to $3.84 over both runs.

## 2026-10-18, question sets v1 and v3 with prompt texts v9, `deepseek-flash`

Both runs started from commit `ffb297f` on 2026-10-18, set v1 at 13:42 UTC and set v3
at 13:50 UTC, with the model, settings, prices and limits of the earlier runs. Since
the runs from `86c5e94`, the retry hint for a block that cites nothing names the block
and quotes its start (#74), and a number on a code fence's own line fails with a hint
of its own (#76), in version 9 of the prompt texts.

| | v1, 11:19 | v1, 13:42 | v3, 11:27 | v3, 13:50 |
| --- | --- | --- | --- | --- |
| Correct | 40 of 44 | 42 of 44 | 40 of 43 | 40 of 43 |
| Released answers counted correct | 28 of 28 | 30 of 30 | 28 of 29 | 28 of 31 |
| Answers to questions that should be refused | 0 of 12 | 0 of 12 | 0 of 12 | 0 of 12 |
| Answerable questions refused | 4 | 2 | 2 | 0 |
| Refusals after three blocked attempts | 1 | 0 | 2 | 0 |
| First attempts that the gate blocked | 14 of 29 | 17 of 30 | 16 of 31 | 15 of 31 |
| Retries | 18 | 20 | 22 | 16 |
| Tokens | 1,729,314 | 1,690,484 | 1,313,673 | 1,237,735 |

No answer stopped after three blocked attempts in either run, and the three questions
that did so at 11:19 and 11:27 were answered:

- `sum-largest-cities-2026` (v1): the hint quoted the table's header row, "| Municipality
  | Population, 1 January 2026 |". The model rewrote the row without a citation, the
  hint quoted it again, and the third attempt cited the source in the row.
- `false-premise-mainland-gdp-shrank-2025` (v3): the hint quoted the opening sentence
  on the premise, and the next attempt cited the source there.
- `ranking-wedding-type-2025` (v3) passed at its first attempt, without the line
  before its table that cited nothing.

Set v1 had 42 of 44 correct: 20 of 22 in Norwegian and 22 of 22 in English. Lookup had
7 of 7, change 5 of 5, share 4 of 5, sum 4 of 5, ranking 5 of 5, false premise 5 of 5,
unanswerable 5 of 5 and out of scope 7 of 7. `ranking-largest-age-group-2026` was
answered from table 05810 with 63,264 tokens, where the runs at 08:07 and 11:19
reached the token limit; its first blocked attempt left its table rows uncited, and
its second wrote places 1 to 5 that no result gives. The two other questions about
age groups, `share-80-plus-norway-2026` and `sum-67-plus-norway-2026`, reached the
token limit as before.

Set v3 had 40 of 43 correct: 21 of 23 in Norwegian and 19 of 20 in English. Lookup had
6 of 7, change 6 of 6, share 5 of 5, sum 5 of 5, ranking 3 of 4, false premise 3 of 4,
unanswerable 8 of 8 and out of scope 4 of 4. The three released answers counted wrong
hold only numbers that the gate traced to cited SSB results or to calculations over
them:

- `lookup-weddings-2025` asks for the marriages of 2025 where the oldest spouse lived
  in Norway. The answer read table 05712 and gave 21 774 marriages where one of the
  partners lived in Norway, and said that the table does not tell who is oldest. At
  08:15 and 11:27 the answer read table 09659, which gives the plan's 21 574.
- `ranking-age-group-highest-wage-2025` read table 11421, whose age bands span five
  years, and joined them into the question's groups with the unweighted mean of the
  bands' averages. The gate traced that calculation, but it is not the average of a
  group, and the answer named "55 years or older" where the plan's group is "55-66
  years".
- `false-premise-women-earn-more-gap-2025` gave the gap as "−6 870 kr per måned
  sammenlignet med menn", women's wage minus men's, as at 11:27.

The two runs used 1,690,484 and 1,237,735 tokens, and the largest correct answer used
95,357 against the agent's limit of 100,000. With every input token counted at the
price without a cache hit, the agent's estimates of the cost are $0.5295 and $0.3949.
The account's balance at DeepSeek fell from $4.11 to $4.01 over both runs.

## 2026-10-18, question sets v1 and v3, `deepseek-flash`

Both runs started from commit `86c5e94` on 2026-10-18, set v1 at 11:19 UTC and set v3
at 11:27 UTC, with the model, settings, prices and limits of the earlier runs. Since
the runs of 08:07 and 08:15 UTC that day, a query refuses a code that SSB reads as
another code (#62), a number in a block that cites nothing gets a retry hint to cite
the result there (#64), a cited calculation gives the numbers of the labels of the
values its steps read (#66), and the scorer accepts a genitive label and decimals left
out as zeros (#67). Set v3 is set v2 with three questions reworded (#68).

| | v1, 08:07 | v1, 11:19 | v2, 08:15 | v3, 11:27 |
| --- | --- | --- | --- | --- |
| Correct | 40 of 44 | 40 of 44 | 33 of 43 | 40 of 43 |
| Released answers counted correct | 28 of 28 | 28 of 28 | 21 of 25 | 28 of 29 |
| Answers to questions that should be refused | 0 of 12 | 0 of 12 | 0 of 12 | 0 of 12 |
| Answerable questions refused | 4 | 4 | 6 | 2 |
| First attempts that the gate blocked | 16 of 29 | 14 of 29 | 17 of 28 | 16 of 31 |
| Retries | 18 | 18 | 20 | 22 |
| Tokens | 1,734,482 | 1,729,314 | 1,382,173 | 1,313,673 |

In set v1, 39 questions were correct in both runs of the day. Of the other two:

- `share-children-norway-2025` was correct. Its first attempt wrote "aged 0 to 6
  [C2]", and the gate passed 0 and 6 through the calculation (#66). The gate blocked
  "1 January 2025" twice, since table 10211, which the model read this time, names no
  day in its labels, and the model left the date out in the third attempt.
- `sum-largest-cities-2026` was refused after three blocked attempts. Each wrote
  "1 January 2026" in the header row of a table that cited no result; the retry hint
  asked the model to cite the source in the same row, and it added the citation to
  the opening sentence instead. At 08:07 the same header row cited the source.

The three questions that reached the token limit ask about age groups, as at 08:07.

In set v3, 40 of the 43 questions were correct: 22 of 23 in Norwegian and 18 of 20 in
English. Lookup had 7 of 7, change 6 of 6, share 5 of 5, sum 5 of 5, ranking 3 of 4,
false premise 2 of 4, unanswerable 8 of 8 and out of scope 4 of 4. Against set v2 at
08:15, eight questions were correct that were not, and one was not that was:

- `lookup-cpi-2026m09` and `ranking-divorce-duration-2025` wrote the same forms as at
  08:15, "104" and "5–9 års", which the scorer now accepts (#67).
- The reworded `sum-divorces-duration-2024-2025` and
  `ranking-age-group-highest-wage-2025` read the tables of the reference plans, 05692
  and 13312 (#68).
- The three questions on table 08484 were answered. At 08:15 one stopped for lack of
  data and two at the token limit.
- `share-divorces-short-marriages-2025` was answered after one retry: the gate had
  blocked a share, 6,9 prosent, that the cited calculation did not give, and the model
  calculated it again.
- `ranking-wedding-type-2025` was refused after three blocked attempts: a line before
  a table, "The full breakdown for 2025, among marriages where the oldest spouse was
  resident in Norway, was:", cited no result, and the model kept it after the hints.

Of the other two, `false-premise-mainland-gdp-shrank-2025` was refused as at 08:15: its
opening sentence on the premise named 2025 and cited no result, and the model sent the
same text after each hint. `false-premise-women-earn-more-gap-2025` asks how large the
difference was, and the answer gave "−6 870 kr i forskjell for kvinner", women's wage
minus men's; the reference plan takes men's minus women's, and the scorer counts the
sign.

All three refusals after blocked attempts in these runs have one cause: a year or a
date in a block that cites no result, a table's header row, a line before a table or
an opening sentence, which the model left uncited after the retry hints. The hints
name the number and its position in the answer and not the block.

The two runs used 1,729,314 and 1,313,673 tokens, and the largest correct answer used
95,111 against the agent's limit of 100,000. With every input token counted at the
price without a cache hit, the agent's estimates of the cost are $0.5399 and $0.4173.
The account's balance at DeepSeek fell from $4.23 to $4.11 over both runs.

## 2026-10-18, question sets v1 and v2, `deepseek-flash`

Both runs started from commit `bd79b18` on 2026-10-18, set v1 at 08:07 UTC and set v2
at 08:15 UTC, with the model, settings, prices and limits of the run of 2026-10-14.
Since that run the agent leaves spent search and metadata results out of later model
calls (#51), the prompt keeps numbers from a false premise out of answers (#52), the
gate passes periods and dates through a calculation and days without a year (#50),
and values are read in their unit's multiplier (#56).

Set v1 against the run of 2026-10-14:

| | 2026-10-14 | 2026-10-18 |
| --- | --- | --- |
| Correct | 27 of 44 | 40 of 44 |
| Released answers | 15, all correct | 28, all correct |
| Answers to questions that should be refused | 0 of 12 | 0 of 12 |
| Answerable questions refused | 17: 12 at the token limit, 5 blocked by the gate | 4: 3 at the token limit, 1 blocked by the gate |
| First attempts that the gate blocked | 25 of 29 | 16 of 29 |
| Retries | 36 | 18 |
| Tokens | 2,790,142 | 1,734,482 |

In set v1, lookup had 7 of 7, change 5 of 5, share 3 of 5, sum 4 of 5, ranking 4 of 5,
false premise 5 of 5, unanswerable 5 of 5 and out of scope 7 of 7; Norwegian 19 of 22
and English 21 of 22. The three questions that reached the token limit ask about age
groups, from tables with a code for each year of age, and wrote no answer before the
limit. The gate blocked the fourth, a share of children aged 0 to 6, three times on
the numbers 0 and 6, which stood in a paragraph that cited only a calculation.

In set v2, 33 of the 43 questions were correct: 18 of 23 in Norwegian and 15 of 20 in
English. Lookup had 5 of 7, change 5 of 6, share 4 of 5, sum 3 of 5, ranking 2 of 4,
false premise 2 of 4, unanswerable 8 of 8 and out of scope 4 of 4.

The agent answered none of the 12 questions of set v2 that should be refused. It
released 25 answers, and the scorer counted 21 as correct. In the other four every
number passed the gate, so each is a value of a cited SSB result or a calculation over
one; they differ from the reference plans as follows:

- `lookup-cpi-2026m09` wrote the index as 104, where the question asks for one
  decimal, 104.0.
- `sum-divorces-2024-2025` gave 16,775 from table 05693, divorces by the number of
  children, where the reference plan reads 16,791 from table 05692, divorces by the
  duration of the marriage. The answer notes that the statistics count, from 2025,
  the events where the older partner lived in Norway.
- `ranking-divorce-duration-2025` named the reference plan's group and number,
  "5–9 års ekteskap" with 1,898 divorces; the scorer looks for the label "5-9 år" as
  a whole word, and the Norwegian genitive "års" runs on from it.
- `ranking-age-highest-wage-2025` named 55–59 years from table 11421, whose age
  groups span five years, where the reference plan ranks the groups of table 13312,
  in which 55-66 years is highest.

It refused 6 of the 31 answerable questions of set v2: 2 at the token limit, 3 after
the gate blocked all three attempts, and 1 for lack of data. Two of the blocked
answers opened with a sentence on the false premise that cited no result, so the year
2025 in it failed; the third wrote the bounds of a duration, 0 and 2, in a paragraph
that cited only a calculation. The refusal for lack of data, `lookup-offences-2025`,
came after the model queried table 08484 with the code `0-999`, whose cell for 2025
is ".", and did not find the code that holds the total, `1AAAAA-9ZZZZz`.

The two runs used 1,734,482 and 1,382,173 tokens. With every input token counted at
the price without a cache hit, the agent's estimates of the cost are $0.5415 and
$0.4379. The account's balance at DeepSeek fell from $4.42 to $4.26 over both runs.

## 2026-10-14, question set v1, `deepseek-flash`

The run started at 17:51 UTC on 2026-10-14 from commit `a690170` and ran every
question of set v1. Inspect called the model `deepseek/deepseek-flash` at the
provider's default endpoint, which named the model `deepseek-flash` in its replies.
Thinking mode was off (`--reasoning-effort none`), the agent's temperature was 0 and
no seed was set. The agent counted cost at $0.30 per million input tokens and $1.20
per million output tokens, DeepSeek's peak prices for input without a cache hit and
for output on that day, with a limit of $0.05 a question. Inspect's limits were
150,000 tokens, 900 seconds and $0.10 a question.

27 of the 44 questions were correct: 16 of 22 in Norwegian and 11 of 22 in English.

| Category | Correct |
| --- | --- |
| lookup | 7 of 7 |
| change | 2 of 5 |
| share | 1 of 5 |
| sum | 2 of 5 |
| ranking | 2 of 5 |
| false premise | 1 of 5 |
| unanswerable | 5 of 5 |
| out of scope | 7 of 7 |

The agent released 15 answers, and all 15 were correct. It answered none of the 12
questions that should be refused. It refused 17 of the 32 questions that have an
answer: 12 when the run reached the limit of 100,000 tokens, and 5 after the gate
blocked all three attempts. The gate blocked the first attempt of 25 of the 29
questions in which the model wrote an answer, and the agent sent 36 retries in all.
No question failed with an error, and none was skipped.

The questions used 2,790,142 tokens. At the prices above, with every input token
counted at the price without a cache hit, the agent's estimate of the cost is
$0.8609.

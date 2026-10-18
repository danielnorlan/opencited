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

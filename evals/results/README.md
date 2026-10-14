# Published runs

Each JSON file here is the report of one published run of the evaluation suite, which
`evals/report.py` wrote from the run's Inspect log. A report names the question set,
the commit, the model and the endpoint, the settings and prices, and the result of
every question ([spec 0005](../../docs/specs/0005-evaluation-suite.md)).

| Report | Question set | Model | Correct |
| --- | --- | --- | --- |
| [2026-10-14T1751-v1-deepseek-deepseek-flash.json](2026-10-14T1751-v1-deepseek-deepseek-flash.json) | v1 | `deepseek-flash` | 27 of 44 |

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

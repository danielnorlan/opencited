"""The run state and the decision after each step, with real values and no model.

Replies are values of the agent's own types, as the model client returns them; the
gate reports come from the gate over recorded evidence.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest
from answering import evidences

from opencited.agent.config import Limits, Prices
from opencited.agent.model import Completion, ToolCall, Usage
from opencited.agent.prompt import ModelRefusal
from opencited.agent.run import (
    MAX_RETRIES,
    CallModel,
    CheckAnswer,
    Decline,
    Fail,
    Refuse,
    Release,
    Retry,
    RunState,
    RunTools,
    cost,
    decide,
)
from opencited.gate import Report, check
from opencited.snapshots import SnapshotStore
from opencited.values import format_number

LIMITS = Limits()
PRICES = Prices(input=Decimal("0.28"), output=Decimal("0.42"))
SEARCH = ToolCall("call_1", "search_tables", '{"query": "befolkning"}')
QUERY = ToolCall("call_2", "get_table_metadata", '{"table_id": "05810"}')
USAGE = Usage(1_200, 80)


def reply(
    text: str | None = None,
    calls: tuple[ToolCall, ...] = (),
    usage: Usage | None = USAGE,
) -> Completion:
    return Completion(text, calls, "stop", "a-model", usage)


@pytest.fixture(scope="module")
def reports(tmp_path_factory: pytest.TempPathFactory) -> tuple[Report, Report]:
    """A report that passes and one that blocks, on the same recorded evidence."""
    found = evidences(SnapshotStore(tmp_path_factory.mktemp("snapshots")))
    value = format_number(found.value("2026"), "no")
    passed = check(f"I 2026 bodde det {value} personer i Norge [E1].", "no", found.evidence)
    blocked = check("I 2026 bodde det 5,7 millioner personer i Norge [E1].", "no", found.evidence)
    assert passed.passed
    assert not blocked.passed
    return passed, blocked


def test_a_new_run_calls_the_model() -> None:
    assert decide(RunState(), LIMITS, None) == CallModel()


def test_a_reply_with_tool_calls_runs_them() -> None:
    state = RunState().replied(reply("Jeg søker først.", (SEARCH, QUERY)))

    assert decide(state, LIMITS, None) == RunTools((SEARCH, QUERY))


def test_after_the_tools_the_model_is_called_again() -> None:
    state = RunState().replied(reply(None, (SEARCH,))).ran(1)

    assert (state.phase, state.tool_calls, state.reply) == ("ready", 1, None)
    assert decide(state, LIMITS, None) == CallModel()


def test_a_reply_without_tool_calls_is_checked() -> None:
    text = "Svar [E1]."
    state = RunState().replied(reply(text))

    assert decide(state, LIMITS, None) == CheckAnswer(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "REFUSE: no data\nSSB har ingen tabell over dette.",
            ModelRefusal("no data", "SSB har ingen tabell over dette."),
        ),
        ("REFUSE: outside the sources", ModelRefusal("outside the sources", "")),
    ],
)
def test_a_refusal_from_the_model_declines(text: str, expected: ModelRefusal) -> None:
    state = RunState().replied(reply(text))

    assert decide(state, LIMITS, None) == Decline(expected)


def test_an_answer_that_passes_is_released(reports: tuple[Report, Report]) -> None:
    passed, _ = reports
    state = RunState().replied(reply("Svar [E1].")).checked(passed, ("E1",))

    assert decide(state, LIMITS, None) == Release("Svar [E1].", passed)


def test_a_blocked_answer_is_retried_twice_and_then_refused(
    reports: tuple[Report, Report],
) -> None:
    _, blocked = reports
    state = RunState()
    decisions: list[object] = []
    for _ in range(MAX_RETRIES + 1):
        state = state.replied(reply("Svar [E1].")).checked(blocked, ("E1",))
        decision = decide(state, LIMITS, None)
        decisions.append(decision)
        state = state.retrying()

    assert decisions == [
        Retry(blocked, "Svar [E1]."),
        Retry(blocked, "Svar [E1]."),
        Refuse("gate blocked", "3 answers in a row were blocked by the gate or cited no result"),
    ]
    assert state.report == blocked


def test_a_retry_calls_the_model_and_keeps_the_last_report(
    reports: tuple[Report, Report],
) -> None:
    _, blocked = reports
    state = RunState().replied(reply("Svar [E1].")).checked(blocked, ("E1",)).retrying()

    assert decide(state, LIMITS, None) == CallModel()
    assert (state.phase, state.report, state.reply) == ("ready", blocked, None)


def test_each_reply_counts_a_model_call_and_its_tokens() -> None:
    state = RunState().replied(reply(None, (SEARCH,))).ran(1).replied(reply("Svar."))

    assert (state.model_calls, state.input_tokens, state.output_tokens, state.tokens) == (
        2,
        2_400,
        160,
        2_560,
    )


def test_the_model_call_limit_stops_before_the_next_call() -> None:
    limits = Limits(model_calls=2)
    state = RunState().replied(reply(None, (SEARCH,))).ran(1)

    assert decide(state, limits, None) == CallModel()
    state = state.replied(reply(None, (SEARCH,))).ran(1)
    assert decide(state, limits, None) == Refuse(
        "limit", "the limit of 2 model calls", "model calls"
    )


@pytest.mark.parametrize(("done", "expected_run"), [(18, True), (19, False)])
def test_the_tool_call_limit_stops_before_tools_that_would_pass_it(
    done: int, expected_run: bool
) -> None:
    state = replace(RunState(), tool_calls=done).replied(reply(None, (SEARCH, QUERY)))

    decision = decide(state, LIMITS, None)

    if expected_run:
        assert decision == RunTools((SEARCH, QUERY))
    else:
        assert decision == Refuse("limit", "the limit of 20 tool calls", "tool calls")


@pytest.mark.parametrize("phase", ["ready", "replied", "checked"])
def test_the_token_limit_stops_the_run_in_every_phase(
    phase: str, reports: tuple[Report, Report]
) -> None:
    passed, _ = reports
    states = {
        "ready": RunState(),
        "replied": RunState().replied(reply("Svar [E1].")),
        "checked": RunState().replied(reply("Svar [E1].")).checked(passed, ("E1",)),
    }
    state = states[phase]
    below = replace(state, input_tokens=99_000, output_tokens=999)
    at = replace(state, input_tokens=99_000, output_tokens=1_000)

    assert not isinstance(decide(below, LIMITS, None), Refuse)
    assert decide(at, LIMITS, None) == Refuse("limit", "the limit of 100,000 tokens", "tokens")


def test_the_time_limit_stops_the_run() -> None:
    state = RunState()

    assert decide(state.at(Decimal("299.999")), LIMITS, None) == CallModel()
    expected = Refuse("limit", "the time limit of 300 seconds", "time")
    assert decide(state.at(Decimal("300.000")), LIMITS, None) == expected
    assert decide(state.at(Decimal("12.000"), timed_out=True), LIMITS, None) == expected


def test_a_time_out_stays_in_the_state() -> None:
    state = RunState().at(Decimal(5), timed_out=True).at(Decimal(6))

    assert state.timed_out


def test_the_cost_limit_stops_the_run_with_prices() -> None:
    limits = Limits(cost=Decimal("0.003738"))
    below = replace(RunState(), input_tokens=12_000, output_tokens=899)
    at = replace(RunState(), input_tokens=12_000, output_tokens=900)

    assert decide(below, limits, PRICES) == CallModel()
    assert decide(at, limits, PRICES) == Refuse("limit", "the cost limit of 0.003738", "cost")
    assert decide(at, Limits(), PRICES) == CallModel()


def test_the_cost_is_exact() -> None:
    state = replace(RunState(), input_tokens=1_234_567, output_tokens=89_012)

    assert cost(state, PRICES) == Decimal("0.3830638")
    assert str(cost(state, PRICES)) == "0.38306380"
    assert cost(state, None) is None


def test_a_limit_stops_the_run_even_with_an_answer_in_hand(
    reports: tuple[Report, Report],
) -> None:
    passed, _ = reports
    state = (
        RunState().replied(reply("Svar [E1].", usage=Usage(99_000, 1_000))).checked(passed, ("E1",))
    )

    assert decide(state, LIMITS, None) == Refuse("limit", "the limit of 100,000 tokens", "tokens")


def test_a_reply_without_usage_fails_the_run() -> None:
    state = RunState().replied(reply("Svar.", usage=None))

    assert state.tokens == 0
    assert decide(state, LIMITS, None) == Fail(
        "the endpoint reported no token usage, so the token limit cannot be kept"
    )


@pytest.mark.parametrize("text", [None, "", "  \n "])
def test_a_reply_with_no_text_and_no_tool_call_fails_the_run(text: str | None) -> None:
    state = RunState().replied(reply(text))

    assert decide(state, LIMITS, None) == Fail(
        "the model replied with neither text nor a tool call"
    )


def test_a_state_that_skips_a_step_is_refused() -> None:
    with pytest.raises(ValueError, match="a replied state holds the reply"):
        decide(replace(RunState(), phase="replied"), LIMITS, None)
    with pytest.raises(ValueError, match="a checked state holds the reply and its report"):
        decide(replace(RunState(), phase="checked"), LIMITS, None)


def test_an_answer_that_cites_nothing_is_retried_like_a_blocked_one(
    reports: tuple[Report, Report],
) -> None:
    passed, _ = reports
    state = RunState()
    decisions: list[object] = []
    for _ in range(MAX_RETRIES + 1):
        state = state.replied(reply("Oslo er hovedstaden.")).checked(passed, ())
        decisions.append(decide(state, LIMITS, None))
        state = state.retrying()

    assert decisions == [
        Retry(passed, "Oslo er hovedstaden.", uncited=True),
        Retry(passed, "Oslo er hovedstaden.", uncited=True),
        Refuse("gate blocked", "3 answers in a row were blocked by the gate or cited no result"),
    ]


@pytest.mark.parametrize("finish", ["length", "content_filter"])
def test_a_reply_that_the_endpoint_cut_off_fails_the_run(finish: str) -> None:
    cut = Completion("I 2026 bodde det", (), finish, "a-model", USAGE)

    assert decide(RunState().replied(cut), LIMITS, None) == Fail(
        f"the endpoint cut the reply off (finish reason {finish})"
    )


def test_a_reply_of_invisible_characters_fails_the_run() -> None:
    state = RunState().replied(reply("\u200b \u2060"))

    assert decide(state, LIMITS, None) == Fail(
        "the model replied with neither text nor a tool call"
    )


def test_each_check_records_what_became_of_the_reply(reports: tuple[Report, Report]) -> None:
    passed, blocked = reports
    answer = reply("Svar [E1].")

    state = (
        RunState()
        .replied(answer)
        .checked(blocked, ("E1",))
        .retrying()
        .replied(answer)
        .checked(passed, ())
        .retrying()
        .replied(answer)
        .checked(passed, ("E1",))
    )

    assert state.attempts == ("blocked", "uncited", "passed")
    assert state.checks == 3

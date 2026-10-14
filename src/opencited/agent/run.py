"""The state of one question's run, and the decision after each step (spec 0004).

The driver in ``opencited.agent.loop`` records what each step gave in the state and
asks ``decide`` what comes next: call the model, run tools, check an answer, retry,
release, refuse or fail. ``decide`` is a plain function of the state, the limits and
the prices, so each limit and each retry is tested with real values and no model.

A limit counts as reached at its value, and the time, token and cost limits are
checked before every decision, so a run that reaches one stops even with an answer
in hand. The model call limit is checked before a model call, and the tool call limit
before the tools of a reply run, all of them or none.

An answer is released only when the gate passes it and it cites at least one result.
The gate passes a text without numbers, and an answer that cites nothing does not
rest on the sources. Such an answer gets a retry as a blocked one does.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal, localcontext
from typing import Literal

from opencited.agent.config import Limits, Prices
from opencited.agent.model import Completion, ToolCall
from opencited.agent.prompt import ModelRefusal, refusal_in
from opencited.gate import Report
from opencited.values import exact_text

# A blocked answer goes back to the model at most this many times.
MAX_RETRIES = 2
# Finish reasons of a reply whose text the endpoint did not finish.
CUT_OFF = frozenset({"length", "content_filter"})
PER_MILLION = 6

type Phase = Literal["ready", "replied", "checked"]
type RefusalReason = Literal["no data", "outside the sources", "gate blocked", "limit"]
type LimitName = Literal["time", "tokens", "cost", "model calls", "tool calls"]


@dataclass(frozen=True)
class RunState:
    """What a run has done so far. ``phase`` says what the last step was.

    ``ready``: the next step is a model call. ``replied``: the model replied, with
    ``reply``. ``checked``: the gate checked the reply, with ``report``. The last
    report stays in the state, since a refusal after a blocked answer holds it.
    """

    phase: Phase = "ready"
    model_calls: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: Decimal = Decimal(0)
    timed_out: bool = False
    checks: int = 0
    reply: Completion | None = None
    report: Report | None = None
    cited: tuple[str, ...] = ()

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def at(self, seconds: Decimal, *, timed_out: bool = False) -> RunState:
        """Return the state at a time since the run started."""
        return replace(self, seconds=seconds, timed_out=self.timed_out or timed_out)

    def replied(self, reply: Completion) -> RunState:
        usage = reply.usage
        return replace(
            self,
            phase="replied",
            model_calls=self.model_calls + 1,
            input_tokens=self.input_tokens + (usage.input_tokens if usage else 0),
            output_tokens=self.output_tokens + (usage.output_tokens if usage else 0),
            reply=reply,
        )

    def ran(self, calls: int) -> RunState:
        return replace(self, phase="ready", tool_calls=self.tool_calls + calls, reply=None)

    def checked(self, report: Report, cited: tuple[str, ...]) -> RunState:
        """Record the gate's report on the reply, and the evidence IDs its markers cite."""
        return replace(self, phase="checked", checks=self.checks + 1, report=report, cited=cited)

    def retrying(self) -> RunState:
        return replace(self, phase="ready", reply=None)


@dataclass(frozen=True)
class CallModel:
    """Send the conversation to the model."""


@dataclass(frozen=True)
class RunTools:
    """Run the tool calls of the last reply and return their results."""

    calls: tuple[ToolCall, ...]


@dataclass(frozen=True)
class CheckAnswer:
    """Check the reply with the evidence gate."""

    text: str


@dataclass(frozen=True)
class Retry:
    """Ask for a corrected answer: with the gate report, or because nothing was cited."""

    report: Report
    uncited: bool = False


@dataclass(frozen=True)
class Release:
    """Release the answer, which the gate passed."""

    text: str
    report: Report


@dataclass(frozen=True)
class Decline:
    """The model refused; its explanation is kept only if the gate passes it."""

    refusal: ModelRefusal


@dataclass(frozen=True)
class Refuse:
    """Stop and refuse. ``detail`` describes the limit or the blocked attempts."""

    reason: RefusalReason
    detail: str
    limit: LimitName | None = None


@dataclass(frozen=True)
class Fail:
    """Stop with an error: the endpoint did something the run cannot go on from."""

    message: str


type Decision = CallModel | RunTools | CheckAnswer | Retry | Release | Decline | Refuse | Fail


def cost(state: RunState, prices: Prices | None) -> Decimal | None:
    """Return the cost of the tokens so far, exactly, or None without prices."""
    if prices is None:
        return None
    # Prices are per million tokens. The precision keeps every product exact.
    with localcontext(prec=100):
        spent = state.input_tokens * prices.input + state.output_tokens * prices.output
        return spent.scaleb(-PER_MILLION)


def limit_reached(state: RunState, limits: Limits, prices: Prices | None) -> Refuse | None:
    """Return the refusal for the time, token or cost limit the run has reached, or None."""
    if state.timed_out or state.seconds >= limits.seconds:
        return Refuse("limit", f"the time limit of {limits.seconds} seconds", "time")
    if state.tokens >= limits.tokens:
        return Refuse("limit", f"the limit of {limits.tokens:,} tokens", "tokens")
    spent = cost(state, prices)
    if limits.cost is not None and spent is not None and spent >= limits.cost:
        return Refuse("limit", f"the cost limit of {exact_text(limits.cost)}", "cost")
    return None


def decide(state: RunState, limits: Limits, prices: Prices | None) -> Decision:
    """Return the next step of a run."""
    reached = limit_reached(state, limits, prices)
    if reached is not None:
        return reached
    match state.phase:
        case "ready":
            if state.model_calls >= limits.model_calls:
                detail = f"the limit of {limits.model_calls} model calls"
                return Refuse("limit", detail, "model calls")
            return CallModel()
        case "replied":
            return after_reply(state, limits)
        case "checked":
            return after_check(state)


def after_reply(state: RunState, limits: Limits) -> Decision:
    reply = state.reply
    if reply is None:
        raise ValueError("a replied state holds the reply")
    if reply.usage is None:
        return Fail("the endpoint reported no token usage, so the token limit cannot be kept")
    if reply.tool_calls:
        if state.tool_calls + len(reply.tool_calls) > limits.tool_calls:
            return Refuse("limit", f"the limit of {limits.tool_calls} tool calls", "tool calls")
        return RunTools(reply.tool_calls)
    if reply.finish_reason in CUT_OFF:
        return Fail(f"the endpoint cut the reply off (finish reason {reply.finish_reason})")
    text = reply.text or ""
    if not any(char.isprintable() and not char.isspace() for char in text):
        return Fail("the model replied with neither text nor a tool call")
    refusal = refusal_in(text)
    if refusal is not None:
        return Decline(refusal)
    return CheckAnswer(text)


def after_check(state: RunState) -> Decision:
    report, reply = state.report, state.reply
    if report is None or reply is None:
        raise ValueError("a checked state holds the reply and its report")
    released = report.passed and bool(state.cited)
    if released:
        return Release(reply.text or "", report)
    if state.checks > MAX_RETRIES:
        detail = f"{state.checks} answers in a row were blocked by the gate or cited no result"
        return Refuse("gate blocked", detail)
    return Retry(report, uncited=report.passed)


__all__ = [
    "MAX_RETRIES",
    "CallModel",
    "CheckAnswer",
    "Decision",
    "Decline",
    "Fail",
    "Refuse",
    "Release",
    "Retry",
    "RunState",
    "RunTools",
    "cost",
    "decide",
]

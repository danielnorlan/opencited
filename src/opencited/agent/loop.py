"""The agent's loop over the model, the toolbox and the evidence gate (spec 0004).

The loop carries out the decisions of ``opencited.agent.run``: it keeps the
conversation, times the run against its time limit and turns the last decision into
an answer or a refusal. It decides nothing itself, so its tests with a model run only
against a real endpoint, in the tests marked ``model``.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from decimal import Decimal

import anyio

from opencited.agent.answer import (
    Answer,
    ModelRecord,
    Refusal,
    RunUsage,
    answer_of,
    model_record,
    usage_of,
)
from opencited.agent.config import ModelConfig
from opencited.agent.model import (
    AssistantMessage,
    Completion,
    Message,
    ModelClient,
    ModelError,
    SystemMessage,
    ToolMessage,
    UserMessage,
)
from opencited.agent.prompt import (
    PROMPT_VERSION,
    retry_message,
    system_prompt,
    uncited_message,
)
from opencited.agent.run import (
    CallModel,
    CheckAnswer,
    Decline,
    Fail,
    LimitName,
    RefusalReason,
    Refuse,
    Release,
    Retry,
    RunState,
    RunTools,
    decide,
)
from opencited.agent.tools import Toolbox
from opencited.gate import Report, check, cited_ids
from opencited.sources import Language

MILLISECOND = Decimal("0.001")

type Clock = Callable[[], int]


class AgentError(Exception):
    """A run that stopped on a fault of the endpoint or the model, with what it used.

    ``usage`` is None when the fault came before the run could count anything.
    """

    def __init__(self, message: str, usage: RunUsage | None) -> None:
        super().__init__(message)
        self.usage = usage


class Run:
    """One question's run: the conversation, the state and the time since it started."""

    def __init__(
        self,
        question: str,
        language: Language,
        model: ModelClient,
        toolbox: Toolbox,
        clock: Clock,
    ) -> None:
        self.question = question
        self.language: Language = language
        self.model = model
        self.toolbox = toolbox
        self.config: ModelConfig = model.config
        self.clock = clock
        self.started = clock()
        self.state = RunState()
        self.reported: list[str] = []
        self.messages: list[Message] = [
            SystemMessage(system_prompt(language)),
            UserMessage(question),
        ]

    def elapsed(self) -> Decimal:
        nanoseconds = Decimal(self.clock() - self.started)
        return nanoseconds.scaleb(-9).quantize(MILLISECOND)

    def remaining(self) -> float:
        # anyio takes seconds as a float; time is not a value from a source.
        return float(self.config.limits.seconds - self.state.seconds)

    def usage(self) -> RunUsage:
        return usage_of(self.state, self.config.prices)

    def record(self) -> ModelRecord:
        return model_record(self.config, self.reported)

    async def call_model(self) -> None:
        reply: Completion | None = None
        with anyio.move_on_after(self.remaining()):
            try:
                reply = await self.model.complete(self.messages, self.toolbox.schemas)
            except ModelError as error:
                failure = str(error)
            else:
                failure = None
            if failure is not None:
                raise AgentError(failure, self.usage())
        if reply is None:
            self.state = self.state.at(self.elapsed(), timed_out=True)
            return
        if reply.model:
            self.reported.append(reply.model)
        self.messages.append(AssistantMessage(reply.text, reply.tool_calls))
        self.state = self.state.replied(reply)

    async def run_tools(self, decision: RunTools) -> None:
        done = 0
        with anyio.move_on_after(self.remaining()) as scope:
            for call in decision.calls:
                outcome = await self.toolbox.run(call)
                self.messages.append(ToolMessage(call.call_id, outcome.text))
                done += 1
        self.state = self.state.ran(done).at(self.elapsed(), timed_out=scope.cancelled_caught)

    def refusal(
        self,
        reason: RefusalReason,
        detail: str,
        *,
        limit: LimitName | None = None,
        explanation: str | None = None,
    ) -> Refusal:
        last: Report | None = self.state.report
        return Refusal(
            question=self.question,
            language=self.language,
            reason=reason,
            detail=detail,
            limit=limit,
            explanation=explanation,
            report=last if last is not None and not last.passed else None,
            attempts=self.state.attempts,
            model=self.record(),
            prompt_version=PROMPT_VERSION,
            usage=self.usage(),
        )

    async def answer(self) -> Answer | Refusal:
        while True:
            self.state = self.state.at(self.elapsed())
            decision = decide(self.state, self.config.limits, self.config.prices)
            match decision:
                case CallModel():
                    await self.call_model()
                case RunTools():
                    await self.run_tools(decision)
                case CheckAnswer():
                    report = check(decision.text, self.language, self.toolbox.evidence)
                    cited = tuple(cited_ids(decision.text, self.language))
                    self.state = self.state.checked(report, cited)
                case Retry():
                    message = (
                        uncited_message() if decision.uncited else retry_message(decision.report)
                    )
                    self.messages.append(UserMessage(message))
                    self.state = self.state.retrying()
                case Release():
                    return answer_of(
                        question=self.question,
                        language=self.language,
                        text=decision.text,
                        report=decision.report,
                        attempts=self.state.attempts,
                        evidence=self.toolbox.evidence,
                        model=self.record(),
                        prompt_version=PROMPT_VERSION,
                        usage=self.usage(),
                    )
                case Decline():
                    explanation = decision.refusal.explanation or None
                    # An explanation may hold numbers too, so the gate checks it.
                    if (
                        explanation is not None
                        and not check(explanation, self.language, self.toolbox.evidence).passed
                    ):
                        explanation = None
                    return self.refusal(
                        decision.refusal.reason, "the model refused", explanation=explanation
                    )
                case Refuse():
                    return self.refusal(decision.reason, decision.detail, limit=decision.limit)
                case Fail():
                    raise AgentError(decision.message, self.usage())


async def ask(
    question: str,
    language: Language,
    *,
    model: ModelClient,
    toolbox: Toolbox,
    clock: Clock = time.monotonic_ns,
) -> Answer | Refusal:
    """Answer one question with the model and the tools, or refuse and say why."""
    return await Run(question, language, model, toolbox, clock).answer()


__all__ = ["AgentError", "ask"]

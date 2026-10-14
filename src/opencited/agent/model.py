"""The model client: chat completions over the project's HTTP stack (ADR 0010).

The client reads the API key from the variable that the configuration names when it
is made, and keeps it only inside the SDK client. No representation, error or log
of this module holds the key. The SDK retries nothing, so every request it sends is
one model call that the agent counts.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any, Literal, Self, cast

import httpx2
from openai import APIConnectionError, APIStatusError, AsyncOpenAI, OpenAIError, omit
from openai.types.chat import (
    ChatCompletion,
    ChatCompletionAssistantMessageParam,
    ChatCompletionFunctionToolParam,
    ChatCompletionMessageParam,
    ChatCompletionMessageToolCallUnionParam,
)

from opencited.agent.config import ModelConfig
from opencited.agent.schemas import flattened
from opencited.http import USER_AGENT

# Answers from a model take longer than responses from SSB; the time limit of a
# question bounds the whole run.
TIMEOUT = httpx2.Timeout(120.0, connect=10.0)
# Headers that the SDK reads from this variable and adds to every request.
CUSTOM_HEADERS = "OPENAI_CUSTOM_HEADERS"
MAX_DETAIL = 300


class ModelError(Exception):
    """A request to the endpoint that failed. The message never holds the API key."""


@dataclass(frozen=True)
class ToolCall:
    """A tool call as the model wrote it. ``arguments`` is the model's JSON text."""

    call_id: str
    name: str
    arguments: str
    kind: Literal["function", "custom"] = "function"


@dataclass(frozen=True)
class Usage:
    """The tokens that the endpoint reports for one model call."""

    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class Completion:
    """A reply: its text, its tool calls, and the model and usage the endpoint reports.

    ``model`` is empty when the endpoint names no model.
    """

    text: str | None
    tool_calls: tuple[ToolCall, ...]
    finish_reason: str | None
    model: str
    usage: Usage | None


@dataclass(frozen=True)
class SystemMessage:
    text: str


@dataclass(frozen=True)
class UserMessage:
    text: str


@dataclass(frozen=True)
class AssistantMessage:
    text: str | None
    tool_calls: tuple[ToolCall, ...] = field(default_factory=tuple[ToolCall, ...])


@dataclass(frozen=True)
class ToolMessage:
    call_id: str
    text: str


type Message = SystemMessage | UserMessage | AssistantMessage | ToolMessage


@dataclass(frozen=True)
class ToolSchema:
    """A tool as the model sees it: a name, a description and a JSON schema."""

    name: str
    description: str
    parameters: Mapping[str, Any]


def tool_param(schema: ToolSchema) -> ChatCompletionFunctionToolParam:
    """Return a tool for the model, with its schema written out (``opencited.agent.schemas``)."""
    return {
        "type": "function",
        "function": {
            "name": schema.name,
            "description": schema.description,
            "parameters": flattened(schema.parameters),
        },
    }


def call_param(call: ToolCall) -> ChatCompletionMessageToolCallUnionParam:
    if call.kind == "custom":
        return {
            "type": "custom",
            "id": call.call_id,
            "custom": {"name": call.name, "input": call.arguments},
        }
    return {
        "type": "function",
        "id": call.call_id,
        "function": {"name": call.name, "arguments": call.arguments},
    }


def message_param(message: Message) -> ChatCompletionMessageParam:
    match message:
        case SystemMessage():
            return {"role": "system", "content": message.text}
        case UserMessage():
            return {"role": "user", "content": message.text}
        case ToolMessage():
            return {"role": "tool", "tool_call_id": message.call_id, "content": message.text}
        case AssistantMessage():
            param: ChatCompletionAssistantMessageParam = {
                "role": "assistant",
                "content": message.text,
            }
            if message.tool_calls:
                param["tool_calls"] = [call_param(call) for call in message.tool_calls]
            return param


def completion_of(response: object) -> Completion:
    """Return the first choice of a response, with its tool calls and usage.

    The SDK builds its models without validating them, so a field the endpoint left
    out or filled with another type is checked here.
    """
    if not isinstance(response, ChatCompletion):
        raise ModelError("the endpoint did not answer with a chat completion; check the base URL")
    if not response.choices:
        raise ModelError("the endpoint returned no choices")
    choice = response.choices[0]
    calls: list[ToolCall] = []
    try:
        for call in choice.message.tool_calls or ():
            if call.type == "function":
                calls.append(ToolCall(call.id, call.function.name, call.function.arguments))
            else:
                calls.append(ToolCall(call.id, call.custom.name, call.custom.input, kind="custom"))
    except AttributeError:
        raise ModelError("the endpoint returned a tool call that could not be read") from None
    usage = response.usage
    used: Usage | None = None
    if usage is not None:
        tokens_in, tokens_out = (
            cast("object", usage.prompt_tokens),
            cast("object", usage.completion_tokens),
        )
        if isinstance(tokens_in, int) and isinstance(tokens_out, int):
            used = Usage(tokens_in, tokens_out)
    finish, model = cast("object", choice.finish_reason), cast("object", response.model)
    return Completion(
        text=choice.message.content,
        tool_calls=tuple(calls),
        finish_reason=finish if isinstance(finish, str) else None,
        model=model if isinstance(model, str) else "",
        usage=used,
    )


def without(text: str, secret: str) -> str:
    return text.replace(secret, "[key]") if secret else text


def detail_of(error: APIStatusError, key: str) -> str:
    """Return the message in an error body, without the key and at most 300 characters.

    The SDK's own message holds the body's repr, which escapes characters and so can
    hide the key from a plain replacement; the message field itself does not.
    """
    body = cast("object", error.body)
    if isinstance(body, dict):
        body = cast("dict[str, object]", body).get("message")
    return without(body, key)[:MAX_DETAIL] if isinstance(body, str) else ""


class ModelClient:
    """Sends conversations to the configured endpoint and returns the replies."""

    def __init__(
        self,
        config: ModelConfig,
        *,
        environ: Mapping[str, str] = os.environ,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        key = environ.get(config.key_variable, "").strip()
        if not key:
            raise ModelError(f"the API key variable {config.key_variable} is not set")
        if any(char.isspace() or not char.isprintable() for char in key):
            raise ModelError(
                f"the API key in {config.key_variable} holds spaces or control characters"
            )
        if os.environ.get(CUSTOM_HEADERS) is not None:
            raise ModelError(
                f"{CUSTOM_HEADERS} is set, and the SDK would send its headers to "
                f"{config.base_url}; unset it to run the agent"
            )
        self.config = config
        self._key = key
        # Empty values keep the SDK from reading an organisation, a project and an
        # admin key from the environment, which belong to another provider.
        self._client = AsyncOpenAI(
            api_key=key,
            admin_api_key="",
            base_url=config.base_url,
            organization="",
            project="",
            max_retries=0,
            # The SDK sets its own user agent on each request, over the HTTP client's.
            default_headers={"User-Agent": USER_AGENT},
            http_client=httpx2.AsyncClient(
                timeout=TIMEOUT,
                follow_redirects=False,
                transport=transport,
            ),
        )
        # With None, the SDK sends no organisation or project header at all.
        self._client.organization = None
        self._client.project = None

    def __repr__(self) -> str:
        return f"ModelClient(base_url={self.config.base_url!r}, model={self.config.model!r})"

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self._client.close()

    async def complete(
        self, messages: Sequence[Message], tools: Sequence[ToolSchema]
    ) -> Completion:
        """Send the conversation and the tools, and return the first choice of the reply."""
        settings = self.config.settings
        # The failure is raised after the except blocks, so that no error chain holds
        # the SDK's exception and with it the request and its key.
        try:
            response = await self._client.chat.completions.create(
                model=self.config.model,
                messages=[message_param(message) for message in messages],
                tools=[tool_param(schema) for schema in tools] if tools else omit,
                # The SDK takes a float. The temperature is a setting, not a value from
                # a source, and is recorded as the decimal that was configured.
                temperature=float(settings.temperature)
                if settings.temperature is not None
                else omit,
                seed=settings.seed if settings.seed is not None else omit,
                extra_body=json.loads(settings.extra) if settings.extra else None,
            )
        except APIStatusError as error:
            failure = self._refused(error)
        except APIConnectionError as error:
            failure = f"the endpoint could not be reached: {without(str(error), self._key)}"
        except (OpenAIError, ValueError) as error:
            # ValueError: a body that is declared as JSON and is not.
            failure = f"the answer could not be read: {without(str(error), self._key)}"
        else:
            return completion_of(response)
        raise ModelError(failure)

    def _refused(self, error: APIStatusError) -> str:
        if error.status_code in (401, 403):
            return (
                f"the endpoint refused the key in {self.config.key_variable} "
                f"(HTTP {error.status_code})"
            )
        detail = detail_of(error, self._key)
        return f"the endpoint answered with HTTP {error.status_code}" + (
            f": {detail}" if detail else ""
        )


__all__ = [
    "AssistantMessage",
    "Completion",
    "Message",
    "ModelClient",
    "ModelError",
    "SystemMessage",
    "ToolCall",
    "ToolMessage",
    "ToolSchema",
    "Usage",
    "UserMessage",
]

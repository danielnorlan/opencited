"""The model client without a model: messages, tool schemas, the key and the request.

Spec 0004 runs the agent with a model only against a real endpoint. These tests send
nothing to one: the transport keeps the request and fails the connection, so no
response from a model is made up.
"""

from __future__ import annotations

import asyncio
import json
import logging
from decimal import Decimal

import httpx2
import pytest
from openai import APIStatusError

from opencited.agent.config import ModelConfig, Settings
from opencited.agent.model import (
    AssistantMessage,
    Message,
    ModelClient,
    ModelError,
    SystemMessage,
    ToolCall,
    ToolMessage,
    ToolSchema,
    UserMessage,
    detail_of,
    message_param,
    tool_param,
    without,
)
from opencited.http import USER_AGENT

KEY = "key-for-the-model-client-test"
CONFIG = ModelConfig(
    base_url="https://models.example.org/v1",
    model="a-model",
    key_variable="A_KEY",
    settings=Settings(temperature=Decimal("0.2"), seed=7),
)
SEARCH = ToolCall("call_1", "search_tables", '{"query":"befolkning"}')
SCHEMA = ToolSchema(
    "get_table_metadata",
    "Describe a table before you query it.",
    {
        "type": "object",
        "properties": {"table_id": {"type": "string", "pattern": "^[0-9]{5}$"}},
        "required": ["table_id"],
    },
)


class Endpoint:
    """A transport that keeps each request and gives a fixed HTTP response, or none.

    Without a response it fails to connect. A response here has a status and a content
    type and no body from any model.
    """

    def __init__(self, response: httpx2.Response | None = None) -> None:
        self.requests: list[httpx2.Request] = []
        self.response = response
        self.transport = httpx2.MockTransport(self.handle)

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if self.response is None:
            raise httpx2.ConnectError("the endpoint is unreachable in tests", request=request)
        return self.response


def attempt(
    config: ModelConfig,
    messages: list[Message],
    tools: list[ToolSchema],
    *,
    response: httpx2.Response | None = None,
    key: str = KEY,
) -> tuple[Endpoint, ModelError]:
    endpoint = Endpoint(response)

    async def run() -> None:
        async with ModelClient(
            config, environ={"A_KEY": key}, transport=endpoint.transport
        ) as client:
            await client.complete(messages, tools)

    with pytest.raises(ModelError) as caught:
        asyncio.run(run())
    return endpoint, caught.value


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            SystemMessage("Answer from the tools."),
            {"role": "system", "content": "Answer from the tools."},
        ),
        (
            UserMessage("Hvor mange bor i Norge?"),
            {"role": "user", "content": "Hvor mange bor i Norge?"},
        ),
        (
            ToolMessage("call_1", '{"evidence_id":"E1"}'),
            {"role": "tool", "tool_call_id": "call_1", "content": '{"evidence_id":"E1"}'},
        ),
        (AssistantMessage("Svar [E1]."), {"role": "assistant", "content": "Svar [E1]."}),
        (
            AssistantMessage(None, (SEARCH,)),
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "type": "function",
                        "id": "call_1",
                        "function": {
                            "name": "search_tables",
                            "arguments": '{"query":"befolkning"}',
                        },
                    }
                ],
            },
        ),
        (
            AssistantMessage(None, (ToolCall("call_2", "query_table", "05810", kind="custom"),)),
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "type": "custom",
                        "id": "call_2",
                        "custom": {"name": "query_table", "input": "05810"},
                    }
                ],
            },
        ),
    ],
)
def test_each_message_becomes_a_chat_message(message: Message, expected: object) -> None:
    assert message_param(message) == expected


def test_a_tool_schema_becomes_a_function_tool() -> None:
    assert tool_param(SCHEMA) == {
        "type": "function",
        "function": {
            "name": "get_table_metadata",
            "description": "Describe a table before you query it.",
            "parameters": SCHEMA.parameters,
        },
    }


@pytest.mark.parametrize("environ", [{}, {"A_KEY": ""}, {"OTHER_KEY": KEY}])
def test_a_client_needs_the_key_in_the_named_variable(environ: dict[str, str]) -> None:
    with pytest.raises(ModelError, match=r"^the API key variable A_KEY is not set$"):
        ModelClient(CONFIG, environ=environ)


def test_custom_headers_from_the_sdk_variable_stop_the_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_CUSTOM_HEADERS", "X-Example: value")

    with pytest.raises(ModelError, match="OPENAI_CUSTOM_HEADERS is set"):
        ModelClient(CONFIG, environ={"A_KEY": KEY})


def test_the_request_goes_once_to_the_configured_endpoint_with_the_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The SDK would read these and send them to any endpoint.
    monkeypatch.setenv("OPENAI_ORG_ID", "org-of-another-provider")
    monkeypatch.setenv("OPENAI_PROJECT_ID", "project-of-another-provider")
    messages: list[Message] = [
        SystemMessage("Answer from the tools."),
        UserMessage("Hvor mange bor i Norge?"),
        AssistantMessage(None, (SEARCH,)),
        ToolMessage("call_1", '{"evidence_id":"E1"}'),
    ]

    endpoint, error = attempt(CONFIG, messages, [SCHEMA])

    assert str(error) == "the endpoint could not be reached: Connection error."
    [request] = endpoint.requests
    assert (request.method, str(request.url)) == (
        "POST",
        "https://models.example.org/v1/chat/completions",
    )
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert request.headers["user-agent"] == USER_AGENT
    assert request.headers["x-stainless-retry-count"] == "0"
    assert "openai-organization" not in request.headers
    assert "openai-project" not in request.headers
    assert json.loads(request.content) == {
        "model": "a-model",
        "messages": [message_param(message) for message in messages],
        "tools": [tool_param(SCHEMA)],
        "temperature": 0.2,
        "seed": 7,
    }


def test_settings_that_are_not_configured_are_not_sent() -> None:
    config = CONFIG.model_copy(update={"settings": Settings()})

    endpoint, _ = attempt(config, [UserMessage("Hvor mange bor i Norge?")], [])

    assert json.loads(endpoint.requests[0].content) == {
        "model": "a-model",
        "messages": [{"role": "user", "content": "Hvor mange bor i Norge?"}],
    }


def test_the_key_is_in_no_representation_error_or_log(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)

    _, error = attempt(CONFIG, [UserMessage("Hvor mange bor i Norge?")], [SCHEMA])
    client = ModelClient(CONFIG, environ={"A_KEY": KEY})

    assert caplog.records
    assert KEY not in caplog.text
    assert KEY not in str(error)
    assert error.__cause__ is None
    assert error.__context__ is None
    assert KEY not in repr(client)
    assert repr(client) == "ModelClient(base_url='https://models.example.org/v1', model='a-model')"


def test_the_key_is_taken_out_of_an_error_text() -> None:
    assert without(f"Incorrect key {KEY} for this model", KEY) == (
        "Incorrect key [key] for this model"
    )
    assert without("no key in this text", "") == "no key in this text"


QUESTION: list[Message] = [UserMessage("Hvor mange bor i Norge?")]


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, "the endpoint refused the key in A_KEY (HTTP 401)"),
        (403, "the endpoint refused the key in A_KEY (HTTP 403)"),
        (429, "the endpoint answered with HTTP 429"),
        (500, "the endpoint answered with HTTP 500"),
    ],
)
def test_an_error_status_is_one_call_and_carries_no_error_chain(status: int, expected: str) -> None:
    endpoint, error = attempt(CONFIG, QUESTION, [], response=httpx2.Response(status))

    assert str(error) == expected
    assert len(endpoint.requests) == 1
    assert (error.__cause__, error.__context__) == (None, None)


def test_an_error_status_carries_the_reason_in_its_body_without_the_key() -> None:
    # The body has the form whose "error" member the SDK unwraps; its text is a test's.
    body = {"error": {"message": f"a reason given by the endpoint for {KEY}"}}

    _, error = attempt(CONFIG, QUESTION, [], response=httpx2.Response(400, json=body))

    assert (
        str(error)
        == "the endpoint answered with HTTP 400: a reason given by the endpoint for [key]"
    )


@pytest.mark.parametrize(
    ("content_type", "expected"),
    [
        ("text/html", "the endpoint did not answer with a chat completion; check the base URL"),
        ("application/json", "the answer could not be read: Expecting value: line 1 column 1"),
    ],
)
def test_an_answer_that_is_not_a_chat_completion_is_an_error(
    content_type: str, expected: str
) -> None:
    response = httpx2.Response(200, headers={"content-type": content_type})

    _, error = attempt(CONFIG, QUESTION, [], response=response)

    assert str(error).startswith(expected)
    assert (error.__cause__, error.__context__) == (None, None)


def test_space_around_the_key_is_dropped() -> None:
    endpoint, _ = attempt(CONFIG, QUESTION, [], key=f"  {KEY}\n")

    assert endpoint.requests[0].headers["authorization"] == f"Bearer {KEY}"


@pytest.mark.parametrize("key", ["two parts", "with\x00null", "with\ttab"])
def test_a_key_with_spaces_or_control_characters_is_refused(key: str) -> None:
    with pytest.raises(ModelError) as caught:
        ModelClient(CONFIG, environ={"A_KEY": key})

    assert str(caught.value) == "the API key in A_KEY holds spaces or control characters"


def status_error(body: object) -> APIStatusError:
    request = httpx2.Request("POST", "https://models.example.org/v1/chat/completions")
    return APIStatusError(
        "Error code: 400", response=httpx2.Response(400, request=request), body=body
    )


def test_the_detail_of_an_error_is_its_message_without_the_key() -> None:
    quoted = 'key-with-"quotes"-and-\\'

    assert detail_of(status_error({"message": f"Incorrect key {quoted}."}), quoted) == (
        "Incorrect key [key]."
    )
    assert detail_of(status_error(f"Incorrect key {KEY}."), KEY) == "Incorrect key [key]."
    assert detail_of(status_error({"code": "invalid"}), KEY) == ""
    assert detail_of(status_error(None), KEY) == ""
    assert detail_of(status_error({"message": "x" * 400}), KEY) == "x" * 300


def test_extra_request_fields_go_into_the_request_body() -> None:
    extra = Settings(extra='{"thinking":{"type":"disabled"}}')
    config = CONFIG.model_copy(update={"settings": extra})

    endpoint, _ = attempt(config, QUESTION, [])

    assert json.loads(endpoint.requests[0].content) == {
        "model": "a-model",
        "messages": [{"role": "user", "content": "Hvor mange bor i Norge?"}],
        "thinking": {"type": "disabled"},
    }

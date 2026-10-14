"""The agent's configuration, read from the environment (spec 0004)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from opencited.agent.config import (
    ConfigurationError,
    Limits,
    ModelConfig,
    Prices,
    Settings,
    from_environment,
)

# example.org is reserved for examples (RFC 2606), so no request can reach anyone.
ENDPOINT = {
    "OPENCITED_MODEL_URL": "https://models.example.org/v1",
    "OPENCITED_MODEL": "a-model",
    "OPENCITED_MODEL_KEY_VARIABLE": "A_KEY",
}


def failure(environ: dict[str, str]) -> str:
    with pytest.raises(ConfigurationError) as caught:
        from_environment(environ)
    return str(caught.value)


def test_the_defaults_are_the_limits_of_spec_0004() -> None:
    config = from_environment(ENDPOINT)

    assert config == ModelConfig(
        base_url="https://models.example.org/v1", model="a-model", key_variable="A_KEY"
    )
    assert config.limits == Limits(model_calls=12, tool_calls=20, tokens=100_000, seconds=300)
    assert config.settings == Settings(temperature=None, seed=None)
    assert config.prices is None


def test_every_setting_comes_from_its_variable() -> None:
    config = from_environment(
        {
            "OPENCITED_MODEL_URL": "http://localhost:11434/v1",
            "OPENCITED_MODEL": "another-model",
            "OPENCITED_MODEL_KEY_VARIABLE": "OTHER_KEY",
            "OPENCITED_TEMPERATURE": "0.2",
            "OPENCITED_SEED": "-7",
            "OPENCITED_PRICE_INPUT": "0.28",
            "OPENCITED_PRICE_OUTPUT": "0.42",
            "OPENCITED_MAX_MODEL_CALLS": "6",
            "OPENCITED_MAX_TOOL_CALLS": "9",
            "OPENCITED_MAX_TOKENS": "50000",
            "OPENCITED_MAX_SECONDS": "120",
            "OPENCITED_MAX_COST": "0.05",
        }
    )

    assert config == ModelConfig(
        base_url="http://localhost:11434/v1",
        model="another-model",
        key_variable="OTHER_KEY",
        settings=Settings(temperature=Decimal("0.2"), seed=-7),
        prices=Prices(input=Decimal("0.28"), output=Decimal("0.42")),
        limits=Limits(
            model_calls=6, tool_calls=9, tokens=50_000, seconds=120, cost=Decimal("0.05")
        ),
    )


def test_decimal_settings_keep_their_digits() -> None:
    config = from_environment(
        ENDPOINT
        | {
            "OPENCITED_TEMPERATURE": "0.70",
            "OPENCITED_PRICE_INPUT": "0.028",
            "OPENCITED_PRICE_OUTPUT": "1.100",
        }
    )

    assert config.prices is not None
    assert str(config.settings.temperature) == "0.70"
    assert (str(config.prices.input), str(config.prices.output)) == ("0.028", "1.100")


@pytest.mark.parametrize("name", sorted(ENDPOINT))
@pytest.mark.parametrize("value", [None, "", "   "])
def test_the_endpoint_the_model_and_the_key_variable_are_required(
    name: str, value: str | None
) -> None:
    environ = {key: item for key, item in ENDPOINT.items() if key != name}
    if value is not None:
        environ[name] = value

    assert failure(environ) == f"{name} is not set"


@pytest.mark.parametrize(
    "name",
    [
        "OPENCITED_SEED",
        "OPENCITED_MAX_MODEL_CALLS",
        "OPENCITED_MAX_TOOL_CALLS",
        "OPENCITED_MAX_TOKENS",
        "OPENCITED_MAX_SECONDS",
    ],
)
@pytest.mark.parametrize("value", ["12.5", "twelve", "1e3", "1_000", "9" * 19, "9" * 5000])
def test_a_whole_number_must_be_written_in_digits(name: str, value: str) -> None:
    assert failure(ENDPOINT | {name: value}) == (
        f"{name} must be a whole number of at most 18 digits, not {value!r}"
    )


@pytest.mark.parametrize(
    "name", ["OPENCITED_TEMPERATURE", "OPENCITED_PRICE_INPUT", "OPENCITED_MAX_COST"]
)
@pytest.mark.parametrize("value", ["0,28", "-0.1", ".5", "1e-3", "NaN", "Infinity"])
def test_a_decimal_must_be_written_with_a_point(name: str, value: str) -> None:
    environ = ENDPOINT | {
        "OPENCITED_PRICE_INPUT": "1",
        "OPENCITED_PRICE_OUTPUT": "1",
        name: value,
    }

    assert failure(environ) == f"{name} must be a decimal number such as 0.28, not {value!r}"


@pytest.mark.parametrize(
    ("name", "value", "reason"),
    [
        ("OPENCITED_MAX_MODEL_CALLS", "0", "greater than or equal to 1"),
        ("OPENCITED_MAX_TOOL_CALLS", "-1", "greater than or equal to 1"),
        ("OPENCITED_MAX_TOKENS", "0", "greater than or equal to 1"),
        ("OPENCITED_MAX_SECONDS", "0", "greater than or equal to 1"),
        ("OPENCITED_TEMPERATURE", "2.01", "less than or equal to 2"),
    ],
)
def test_a_setting_out_of_range_names_its_variable(name: str, value: str, reason: str) -> None:
    assert failure(ENDPOINT | {name: value}) == f"{name}: Input should be {reason}"


@pytest.mark.parametrize("name", ["OPENCITED_PRICE_INPUT", "OPENCITED_PRICE_OUTPUT"])
def test_prices_are_set_together(name: str) -> None:
    assert failure(ENDPOINT | {name: "0.28"}) == (
        "set both OPENCITED_PRICE_INPUT and OPENCITED_PRICE_OUTPUT, or neither"
    )


def test_a_cost_limit_needs_prices() -> None:
    assert failure(ENDPOINT | {"OPENCITED_MAX_COST": "0.05"}) == (
        "a cost limit needs prices: set OPENCITED_PRICE_INPUT and OPENCITED_PRICE_OUTPUT"
    )


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("ftp://models.example.org/v1", "the base URL must be an http or https URL"),
        ("models.example.org/v1", "the base URL must be an http or https URL"),
        ("https:///v1", "the base URL must be an http or https URL"),
        ("https://name:secret@localhost/v1", "may not hold a user name or password"),
        ("https://token@localhost:8080/v1", "may not hold a user name or password"),
        ("https://models.example.org/v1?key=secret", "may not have a query or a fragment"),
        ("https://models.example.org/v1#part", "may not have a query or a fragment"),
    ],
)
def test_the_base_url_holds_no_secret_and_has_a_web_scheme(url: str, reason: str) -> None:
    message = failure(ENDPOINT | {"OPENCITED_MODEL_URL": url})

    assert message.startswith("OPENCITED_MODEL_URL: ")
    assert reason in message
    assert url not in message


def test_a_refused_url_is_in_no_error_chain() -> None:
    url = "ftp://name:secret@localhost/v1?key=secret"

    with pytest.raises(ConfigurationError) as caught:
        from_environment(ENDPOINT | {"OPENCITED_MODEL_URL": url})

    assert "secret" not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


@pytest.mark.parametrize(
    "name", ["1KEY", "A-KEY", "A KEY", "$A_KEY", "lower_case", "gsk_Abc123", "K" * 65]
)
def test_the_key_variable_is_a_variable_name(name: str) -> None:
    message = failure(ENDPOINT | {"OPENCITED_MODEL_KEY_VARIABLE": name})

    assert message.startswith("OPENCITED_MODEL_KEY_VARIABLE: String should match pattern")


def test_the_configuration_names_the_key_variable_and_never_holds_the_key() -> None:
    key = "key-for-the-configuration-test"
    config = from_environment(ENDPOINT | {"A_KEY": key})

    assert config.key_variable == "A_KEY"
    assert key not in repr(config)
    assert key not in config.model_dump_json()


def test_extra_request_fields_are_kept_as_canonical_json() -> None:
    config = from_environment(
        ENDPOINT | {"OPENCITED_MODEL_EXTRA": ' { "thinking" : { "type" : "disabled" } } '}
    )

    assert config.settings.extra == '{"thinking":{"type":"disabled"}}'


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        (
            '{"thinking": ',
            "the extra request fields are not JSON: Expecting value: line 1 column 13 (char 12)",
        ),
        ('["thinking"]', "the extra request fields must be a JSON object"),
        ('{"model": "another", "messages": []}', "the client sets messages, model itself"),
        ('{"temperature": 1}', "the client sets temperature itself"),
    ],
)
def test_extra_request_fields_must_be_an_object_the_client_does_not_set(
    value: str, reason: str
) -> None:
    assert failure(ENDPOINT | {"OPENCITED_MODEL_EXTRA": value}) == (
        f"OPENCITED_MODEL_EXTRA: {reason}"
    )

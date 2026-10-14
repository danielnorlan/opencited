"""The agent's configuration: endpoint, model, settings, prices and limits (spec 0004).

Everything is read from environment variables, so a change of endpoint, model or key
needs no code change. The configuration holds the name of the variable with the API
key and never the key, which only the model client reads.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from decimal import Decimal
from typing import Annotated, Self
from urllib.parse import urlsplit

from pydantic import Field, StringConstraints, ValidationError, field_validator, model_validator
from pydantic_core import ErrorDetails

from opencited.model import Frozen
from opencited.values import ExactDecimal

MODEL_URL = "OPENCITED_MODEL_URL"
MODEL = "OPENCITED_MODEL"
KEY_VARIABLE = "OPENCITED_MODEL_KEY_VARIABLE"
TEMPERATURE = "OPENCITED_TEMPERATURE"
SEED = "OPENCITED_SEED"
PRICE_INPUT = "OPENCITED_PRICE_INPUT"
PRICE_OUTPUT = "OPENCITED_PRICE_OUTPUT"
MAX_MODEL_CALLS = "OPENCITED_MAX_MODEL_CALLS"
MAX_TOOL_CALLS = "OPENCITED_MAX_TOOL_CALLS"
MAX_TOKENS = "OPENCITED_MAX_TOKENS"
MAX_SECONDS = "OPENCITED_MAX_SECONDS"
MAX_COST = "OPENCITED_MAX_COST"

# Upper case and short, so that a key pasted in place of the name is refused before
# any message repeats it.
VARIABLE_NAME = r"^[A-Z_][A-Z0-9_]{0,63}$"
WHOLE = re.compile(r"-?[0-9]{1,18}")
DECIMAL = re.compile(r"[0-9]+(\.[0-9]+)?")

VARIABLES: dict[tuple[str, ...], str] = {
    ("base_url",): MODEL_URL,
    ("model",): MODEL,
    ("key_variable",): KEY_VARIABLE,
    ("settings", "temperature"): TEMPERATURE,
    ("settings", "seed"): SEED,
    ("prices", "input"): PRICE_INPUT,
    ("prices", "output"): PRICE_OUTPUT,
    ("limits", "model_calls"): MAX_MODEL_CALLS,
    ("limits", "tool_calls"): MAX_TOOL_CALLS,
    ("limits", "tokens"): MAX_TOKENS,
    ("limits", "seconds"): MAX_SECONDS,
    ("limits", "cost"): MAX_COST,
}

NonNegative = Annotated[ExactDecimal, Field(ge=0)]


class ConfigurationError(Exception):
    """A setting that is missing or cannot be read. The message names the variable."""


class Settings(Frozen):
    """Model settings that change results, recorded in every answer."""

    temperature: Annotated[ExactDecimal, Field(ge=0, le=2)] | None = None
    seed: int | None = None


class Prices(Frozen):
    """Prices per million input tokens and per million output tokens, in one currency."""

    input: NonNegative
    output: NonNegative


class Limits(Frozen):
    """The limits of one question. The cost limit is in the currency of the prices."""

    model_calls: int = Field(default=12, ge=1)
    tool_calls: int = Field(default=20, ge=1)
    tokens: int = Field(default=100_000, ge=1)
    seconds: int = Field(default=300, ge=1)
    cost: NonNegative | None = None


class ModelConfig(Frozen):
    """The endpoint and the model, and the name of the variable that holds the key."""

    base_url: str
    model: Annotated[str, StringConstraints(min_length=1)]
    key_variable: Annotated[str, StringConstraints(pattern=VARIABLE_NAME)]
    settings: Settings = Settings()
    prices: Prices | None = None
    limits: Limits = Limits()

    @field_validator("base_url")
    @classmethod
    def check_url(cls, url: str) -> str:
        # A user name, password or query in the URL would put a secret wherever the
        # URL is logged, so the key travels only in its own variable.
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            # The URL is not repeated, since it may hold a secret.
            raise ValueError("the base URL must be an http or https URL with a host")
        if parts.username is not None or parts.password is not None:
            raise ValueError("the base URL may not hold a user name or password")
        if parts.query or parts.fragment:
            raise ValueError("the base URL may not have a query or a fragment")
        return url

    @model_validator(mode="after")
    def check_cost(self) -> Self:
        if self.limits.cost is not None and self.prices is None:
            raise ValueError(f"a cost limit needs prices: set {PRICE_INPUT} and {PRICE_OUTPUT}")
        return self


def required(environ: Mapping[str, str], name: str) -> str:
    value = environ.get(name, "").strip()
    if not value:
        raise ConfigurationError(f"{name} is not set")
    return value


def whole(environ: Mapping[str, str], name: str) -> int | None:
    value = environ.get(name, "").strip()
    if not value:
        return None
    if not WHOLE.fullmatch(value):
        raise ConfigurationError(
            f"{name} must be a whole number of at most 18 digits, not {value!r}"
        )
    return int(value)


def decimal(environ: Mapping[str, str], name: str) -> Decimal | None:
    value = environ.get(name, "").strip()
    if not value:
        return None
    if not DECIMAL.fullmatch(value):
        raise ConfigurationError(f"{name} must be a decimal number such as 0.28, not {value!r}")
    return Decimal(value)


def from_environment(environ: Mapping[str, str]) -> ModelConfig:
    """Return the configuration in the environment, with the defaults of spec 0004."""
    prices_in, prices_out = decimal(environ, PRICE_INPUT), decimal(environ, PRICE_OUTPUT)
    if (prices_in is None) != (prices_out is None):
        raise ConfigurationError(f"set both {PRICE_INPUT} and {PRICE_OUTPUT}, or neither")
    values: dict[str, object] = {
        "base_url": required(environ, MODEL_URL),
        "model": required(environ, MODEL),
        "key_variable": required(environ, KEY_VARIABLE),
        "settings": present(temperature=decimal(environ, TEMPERATURE), seed=whole(environ, SEED)),
        "limits": present(
            model_calls=whole(environ, MAX_MODEL_CALLS),
            tool_calls=whole(environ, MAX_TOOL_CALLS),
            tokens=whole(environ, MAX_TOKENS),
            seconds=whole(environ, MAX_SECONDS),
            cost=decimal(environ, MAX_COST),
        ),
    }
    if prices_in is not None:
        values["prices"] = {"input": prices_in, "output": prices_out}
    try:
        return ModelConfig.model_validate(values)
    except ValidationError as error:
        reasons = "; ".join(explain(problem) for problem in error.errors())
    # Raised outside the except block, so that no error chain holds Pydantic's error,
    # which repeats each value it refused.
    raise ConfigurationError(reasons)


def present(**values: object) -> dict[str, object]:
    return {name: value for name, value in values.items() if value is not None}


def explain(problem: ErrorDetails) -> str:
    """Name the variable that a validation error is about, and the reason."""
    reason = problem["msg"].removeprefix("Value error, ")
    variable = VARIABLES.get(tuple(str(part) for part in problem["loc"]))
    return f"{variable}: {reason}" if variable else reason


__all__ = [
    "ConfigurationError",
    "Limits",
    "ModelConfig",
    "Prices",
    "Settings",
    "from_environment",
]

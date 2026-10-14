"""Tool schemas written out for the model, and their equivalence with the published ones."""

from __future__ import annotations

import json
from typing import Any

import jsonschema
import pytest

from opencited.agent.schemas import flattened
from opencited.agent.tools import calculate_schema

LANGUAGE = {
    "type": "object",
    "$defs": {"Language": {"enum": ["no", "en"], "type": "string"}},
    "properties": {
        "query": {"title": "Query", "type": "string"},
        "language": {"$ref": "#/$defs/Language", "default": "no", "description": "The language."},
    },
    "required": ["query"],
    "title": "search_tablesArguments",
}
STEP = {"name": "a", "snapshot": "E1"}
PLANS: list[dict[str, Any]] = [
    {"steps": [STEP | {"operation": "value", "codes": {"Tid": "2026"}}]},
    {"steps": [STEP | {"operation": "sum", "selection": {"Tid": ["2025", "2026"]}}]},
    {
        "steps": [
            STEP
            | {
                "operation": "rank",
                "selection": {"Tid": ["2026"]},
                "by": "Region",
                "order": "highest",
                "count": 3,
            }
        ]
    },
    {
        "steps": [
            {"name": "g", "operation": "change_percent", "old": "a", "new": "b", "decimals": 2}
        ]
    },
    {"steps": [STEP | {"operation": "value"}]},
    {"steps": [STEP | {"operation": "avg", "codes": {}}]},
    {"steps": [STEP | {"operation": "value", "codes": {}, "decimals": 2}]},
    {"steps": [{"name": "g", "operation": "divide", "left": "a", "right": "b", "decimals": 21}]},
    {"steps": []},
    {
        "steps": [
            STEP
            | {"operation": "rank", "selection": {}, "by": "Region", "order": "top", "count": 3}
        ]
    },
]


def valid(schema: dict[str, Any], plan: dict[str, Any]) -> bool:
    try:
        jsonschema.validate(plan, schema, cls=jsonschema.Draft202012Validator)
    except jsonschema.ValidationError:
        return False
    return True


def text_of(schema: object) -> str:
    return json.dumps(schema)


def test_the_plan_schema_is_written_out_without_references() -> None:
    flat = flattened(calculate_schema())
    written = text_of(flat)

    for keyword in ('"$ref"', '"$defs"', '"oneOf"', '"const"', '"title"', '"discriminator"'):
        assert keyword not in written
    branches = flat["properties"]["steps"]["items"]["anyOf"]
    assert len(branches) == 8
    assert {"value", "sum", "mean", "rank", "divide"} <= {
        value for branch in branches for value in branch["properties"]["operation"]["enum"]
    }


@pytest.mark.parametrize("plan", PLANS)
def test_the_written_out_schema_accepts_the_same_plans(plan: dict[str, Any]) -> None:
    published = valid(calculate_schema(), plan)
    flat = valid(flattened(calculate_schema()), plan)

    assert flat is published


def test_keys_beside_a_reference_are_kept() -> None:
    flat = flattened(LANGUAGE)

    assert flat == {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "language": {
                "enum": ["no", "en"],
                "type": "string",
                "default": "no",
                "description": "The language.",
            },
        },
        "required": ["query"],
    }


def test_a_property_named_like_a_keyword_keeps_its_name() -> None:
    schema = {
        "type": "object",
        "properties": {"title": {"type": "string"}, "const": {"type": "integer"}},
    }

    assert flattened(schema) == schema


def test_a_reference_to_nothing_or_to_itself_is_refused() -> None:
    with pytest.raises(ValueError, match="refers to #/\\$defs/Missing, which it does not define"):
        flattened({"properties": {"a": {"$ref": "#/$defs/Missing"}}})
    looped = {
        "$defs": {"Node": {"properties": {"next": {"$ref": "#/$defs/Node"}}}},
        "$ref": "#/$defs/Node",
    }
    with pytest.raises(ValueError, match="too deeply"):
        flattened(looped)

"""Tool schemas as the model gets them: self-contained, in the common part of JSON Schema.

The published schemas come from Pydantic and use ``$defs`` with ``$ref``, ``oneOf``
with a discriminator, and ``const``. Not every endpoint reads those: Inspect's agent
bridge keeps only types, properties, items, ``anyOf``, ``enum``, ``required`` and a
few limits, and would turn the schema of a calculation plan into a list of anything.
So the model gets each schema with every reference written out, ``oneOf`` as
``anyOf`` and ``const`` as a one-value ``enum``, without titles. The agent still
checks every call against the published schema.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

# Keys that only name or document a schema for code, or that the flat form replaces.
DROPPED = frozenset({"title", "discriminator", "$defs"})
# A reference cycle would write itself out forever; no published schema has one.
MAX_DEPTH = 32


def flattened(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Return a schema with its references written out and only common keywords."""
    definitions = cast("Mapping[str, Any]", schema.get("$defs", {}))
    return cast("dict[str, Any]", rewrite(schema, definitions, 0))


def rewrite(node: object, definitions: Mapping[str, Any], depth: int) -> object:
    if depth > MAX_DEPTH:
        raise ValueError("the schema nests or refers to itself too deeply to write out")
    if isinstance(node, list):
        return [rewrite(item, definitions, depth + 1) for item in cast("list[object]", node)]
    if not isinstance(node, dict):
        return node
    source = cast("dict[str, Any]", node)
    reference = source.get("$ref")
    if isinstance(reference, str):
        name = reference.removeprefix("#/$defs/")
        if name == reference or name not in definitions:
            raise ValueError(f"the schema refers to {reference}, which it does not define")
        # Keys beside the reference, such as a description or a default, are kept.
        source = {
            **definitions[name],
            **{key: value for key, value in source.items() if key != "$ref"},
        }
    result: dict[str, Any] = {}
    for key, value in source.items():
        if key in DROPPED:
            continue
        if key == "oneOf":
            result["anyOf"] = rewrite(value, definitions, depth + 1)
        elif key == "const":
            result["enum"] = [value]
        elif key == "properties" and isinstance(value, dict):
            properties = cast("dict[str, object]", value)
            result[key] = {
                name: rewrite(item, definitions, depth + 1) for name, item in properties.items()
            }
        else:
            result[key] = rewrite(value, definitions, depth + 1)
    return result


__all__ = ["flattened"]

"""The agent's tools: the three tools of the MCP server, and calculate (spec 0004).

The toolbox calls the server's tools through the official MCP client and runs
calculation plans in the engine. It runs only calls to these four tools whose
arguments match the published schema, and gives every result an evidence ID: E1, E2
and so on for source results, C1, C2 and so on for calculations. The model writes
these IDs in plans and in citation markers, and the toolbox replaces the IDs in a
plan with snapshot IDs before the engine runs it.

Values reach the model as decimal strings and formatted for the language of the
answer. Text from the sources, such as titles and notes, reaches it as data inside
the result.
"""

from __future__ import annotations

import copy
import json
import logging
import unicodedata
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal, cast

import anyio.to_thread
import jsonschema
from mcp import Client
from mcp.types import CallToolResult, TextContent, Tool
from pydantic import ValidationError

from opencited.agent.model import ToolCall, ToolSchema
from opencited.calc.engine import CalculationError, Engine
from opencited.calc.plan import Plan, plan_schema
from opencited.calc.result import CalculationResult
from opencited.gate import CalculationEvidence, Evidence, SourceEvidence
from opencited.snapshots import SnapshotError, SnapshotStore
from opencited.sources import Language, QueryResult, SearchResult, TableMetadata
from opencited.values import format_number

logger = logging.getLogger("opencited.agent")

SERVER_TOOLS: Mapping[str, type[QueryResult | TableMetadata | SearchResult]] = {
    "search_tables": SearchResult,
    "get_table_metadata": TableMetadata,
    "query_table": QueryResult,
}
CALCULATE = "calculate"
CALCULATE_DESCRIPTION = (
    "Run a calculation plan of 1 to 20 steps over query results. Each step has a name and an operation, "
    "and reads the cells of a query_table result, named by its evidence ID such as E1, "
    "or the results of earlier steps, named by their step names. The arithmetic is "
    "exact, and a step that states decimals rounds once, with ties away from zero. "
    "Returns every step with its result as a decimal string and formatted, and an "
    "evidence ID for the calculation."
)
EVIDENCE_ID = {
    "type": "string",
    "pattern": r"^E[1-9][0-9]*$",
    "description": "The evidence ID of a query_table result, such as E1.",
}

# The deepest plan step nests five levels: arguments, steps, a step, its selection
# and a list of codes.
MAX_DEPTH = 20

type Status = Literal["result", "failed", "refused"]


class RefusalError(Exception):
    """A call that the toolbox does not run, with the reason the model is given."""


class ToolFailureError(Exception):
    """A call that ran and failed, with the reason the model is given."""


@dataclass(frozen=True)
class ToolOutcome:
    """The tool message for one call, and its evidence when the call gave a result."""

    call: ToolCall
    status: Status
    text: str
    evidence: Evidence | None = None


def calculate_schema() -> dict[str, Any]:
    """Return the schema of a plan, with an evidence ID wherever a step names a snapshot."""
    schema = copy.deepcopy(plan_schema())
    for definition in cast("dict[str, dict[str, Any]]", schema["$defs"]).values():
        properties = cast("dict[str, Any]", definition.get("properties", {}))
        if "snapshot" in properties:
            properties["snapshot"] = dict(EVIDENCE_ID)
    return schema


def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    # JSON parsers keep the last of two equal keys, which would hide a choice.
    keys = [key for key, _ in pairs]
    repeated = sorted({key for key in keys if keys.count(key) > 1})
    if repeated:
        raise ValueError(f"the arguments name {', '.join(repeated)} more than once in one object")
    return dict(pairs)


def no_constant(name: str) -> object:
    raise ValueError(f"the arguments hold {name}, which is not a number")


def depth(value: object) -> int:
    """Return how deeply objects and arrays nest in parsed JSON, without recursion."""
    deepest = 0
    pending: list[tuple[object, int]] = [(value, 1)]
    while pending and deepest <= MAX_DEPTH:
        item, level = pending.pop()
        if isinstance(item, dict):
            deepest = max(deepest, level)
            pending.extend((child, level + 1) for child in cast("dict[str, object]", item).values())
        elif isinstance(item, list):
            deepest = max(deepest, level)
            pending.extend((child, level + 1) for child in cast("list[object]", item))
    return deepest


def arguments_of(text: str) -> dict[str, Any]:
    """Parse a call's arguments, with numbers as decimals or integers and never as floats."""
    try:
        value = json.loads(
            text or "{}",
            object_pairs_hook=unique,
            parse_float=Decimal,
            parse_constant=no_constant,
        )
    except json.JSONDecodeError as error:
        raise RefusalError(f"the arguments are not valid JSON: {error}") from error
    except RecursionError as error:
        raise RefusalError("the arguments are nested too deeply") from error
    except ValueError as error:
        # A repeated key, NaN or an infinity, or an integer too long to convert.
        raise RefusalError(str(error)) from error
    if depth(value) > MAX_DEPTH:
        # Whether the parser refuses deep nesting depends on the Python build, and the
        # checks after this one walk the arguments recursively.
        raise RefusalError("the arguments are nested too deeply")
    if not isinstance(value, dict):
        raise RefusalError("the arguments are not a JSON object")
    return cast("dict[str, Any]", value)


def check_arguments(schema: ToolSchema, arguments: Mapping[str, Any]) -> None:
    """Refuse arguments that the schema does not name, or that do not match it."""
    properties = cast("Mapping[str, Any]", schema.parameters.get("properties", {}))
    unknown = sorted(set(arguments) - set(properties))
    if unknown:
        raise RefusalError(f"{schema.name} takes no argument {', '.join(unknown)}")
    if any(controlled(text) for text in texts(arguments)):
        # In Python a pattern's $ also matches before a final line break, so
        # "05810\n" would match the table ID pattern ^[0-9]{5}$. No argument needs one.
        raise RefusalError("the arguments hold a control character, such as a line break")
    try:
        validate(arguments, schema.parameters)
    except jsonschema.ValidationError as error:
        raise RefusalError(explain(error, schema.parameters)) from error


def texts(value: object) -> Iterator[str]:
    """Yield every string in parsed JSON, keys included."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in cast("dict[str, object]", value).items():
            yield key
            yield from texts(item)
    elif isinstance(value, list):
        for item in cast("list[object]", value):
            yield from texts(item)


def controlled(text: str) -> bool:
    return any(unicodedata.category(char) == "Cc" for char in text)


def validate(instance: object, schema: Mapping[str, Any]) -> None:
    # validate() raises the error that best explains the mismatch.
    jsonschema.validate(instance, schema, cls=jsonschema.Draft202012Validator)


def explain(
    error: jsonschema.ValidationError, root: Mapping[str, Any], within: Sequence[str | int] = ()
) -> str:
    """Say why the arguments do not match, with the path to the part that does not.

    A JSON schema validator does not read the discriminator that Pydantic writes beside
    ``oneOf``, and would only say that a step of a plan matches none of the operations.
    For such a step, the reason is why it does not match the operation it names.
    """
    path = [*within, *error.absolute_path]
    discriminator = cast("Mapping[str, Any]", error.schema).get("discriminator")
    instance = cast("object", error.instance)
    if error.validator == "oneOf" and isinstance(instance, dict) and discriminator:
        named = cast("dict[str, Any]", discriminator)
        instance = cast("dict[str, Any]", instance)
        mapping = cast("dict[str, str]", named["mapping"])
        name = cast("str", named["propertyName"])
        if name not in instance:
            return located(path, f"{name!r} is a required property")
        target = mapping.get(str(instance[name]))
        if target is None:
            return located(
                [*path, name], f"{instance[name]!r} is not one of {', '.join(sorted(mapping))}"
            )
        try:
            validate(instance, {"$defs": root.get("$defs", {}), "$ref": target})
        except jsonschema.ValidationError as inner:
            return explain(inner, root, path)
    return located(path, error.message)


def located(path: Sequence[str | int], message: str) -> str:
    where = "/".join(str(part) for part in path)
    return f"{where}: {message}" if where else message


def error_text(result: CallToolResult) -> str:
    texts = [part.text for part in result.content if isinstance(part, TextContent)]
    return " ".join(texts) or "the tool reported an error without a reason"


def compact_dimension(dimension: dict[str, Any]) -> dict[str, Any]:
    """Return a dimension with its values as a map from code to label.

    A table such as 06913 has 1,288 regions, and each value written out as an object
    with its code, label and notes doubles what the model reads in every later call.
    A value's notes stay, under ``value_notes``, where it has any.
    """
    values = cast("list[dict[str, Any]]", dimension["values"])
    shown = {key: item for key, item in dimension.items() if key != "values"}
    shown["values"] = {value["code"]: value["label"] for value in values}
    notes = {value["code"]: value["notes"] for value in values if value["notes"]}
    if notes:
        shown["value_notes"] = notes
    return shown


def source_view(
    evidence_id: str, result: QueryResult | TableMetadata | SearchResult, language: Language
) -> dict[str, Any]:
    """Return a source result for the model, with every value also formatted.

    The gate reads the result itself; this view only changes how the model reads it.
    """
    data = result.model_dump(mode="json")
    if isinstance(result, QueryResult | TableMetadata):
        dimensions = cast("list[dict[str, Any]]", data["dimensions"])
        data["dimensions"] = [compact_dimension(dimension) for dimension in dimensions]
    if isinstance(result, QueryResult):
        cells = cast("list[dict[str, Any]]", data["observations"])
        for cell, observation in zip(cells, result.observations, strict=True):
            if observation.value is not None:
                cell["formatted"] = format_number(observation.value, language)
    return {"evidence_id": evidence_id, **data}


def calculation_view(
    evidence_id: str,
    result: CalculationResult,
    reads: Mapping[str, str],
    language: Language,
) -> dict[str, Any]:
    """Return a calculation for the model, with the evidence ID that each step read."""
    data = result.model_dump(mode="json")
    for view, step in zip(cast("list[dict[str, Any]]", data["steps"]), result.steps, strict=True):
        if step.name in reads:
            view["read"] = reads[step.name]
        if step.value is not None:
            view["formatted"] = format_number(step.value, language)
        cells = cast("list[dict[str, Any]]", view["ranked"])
        for cell, ranked in zip(cells, step.ranked, strict=True):
            cell["formatted"] = format_number(ranked.value, language)
    return {"evidence_id": evidence_id, **data}


def compact(view: Mapping[str, Any]) -> str:
    return json.dumps(view, ensure_ascii=False, separators=(",", ":"))


class Toolbox:
    """The four tools of one run, and the evidence of every result they gave."""

    def __init__(
        self,
        client: Client,
        server_tools: Sequence[Tool],
        store: SnapshotStore,
        language: Language,
    ) -> None:
        found = {tool.name: tool for tool in server_tools}
        missing = [name for name in SERVER_TOOLS if name not in found]
        if missing:
            raise ValueError(f"the MCP server has no tool {', '.join(missing)}")
        # Only these tools, in this order: the model gets no other tool that a server
        # may offer.
        self.schemas = (
            *(
                ToolSchema(name, found[name].description or "", found[name].input_schema)
                for name in SERVER_TOOLS
            ),
            ToolSchema(CALCULATE, CALCULATE_DESCRIPTION, calculate_schema()),
        )
        self.client = client
        self.engine = Engine(store)
        self.store = store
        self.language: Language = language
        self._evidence: list[Evidence] = []
        self._sources: dict[str, QueryResult | TableMetadata | SearchResult] = {}

    @classmethod
    async def connect(cls, client: Client, store: SnapshotStore, language: Language) -> Toolbox:
        """Return the toolbox for a connected MCP client, with the tools the server lists."""
        tools: list[Tool] = []
        cursor: str | None = None
        while True:
            listed = await client.list_tools(cursor=cursor)
            tools.extend(listed.tools)
            cursor = listed.next_cursor
            if cursor is None:
                return cls(client, tools, store, language)

    @property
    def evidence(self) -> tuple[Evidence, ...]:
        """Every result of the run in order, with its evidence ID, as the gate takes it."""
        return tuple(self._evidence)

    async def run(self, call: ToolCall) -> ToolOutcome:
        """Run one call and return the tool message for it."""
        try:
            schema = self._schema(call)
            arguments = arguments_of(call.arguments)
            check_arguments(schema, arguments)
            if call.name == CALCULATE:
                evidence, view = await self._calculate(arguments)
            else:
                evidence, view = await self._call(call.name, arguments)
        except RefusalError as reason:
            return ToolOutcome(call, "refused", f"The call was not run: {reason}")
        except ToolFailureError as reason:
            return ToolOutcome(call, "failed", f"The tool failed: {reason}")
        self._evidence.append(evidence)
        return ToolOutcome(call, "result", compact(view), evidence)

    def _schema(self, call: ToolCall) -> ToolSchema:
        if call.kind != "function":
            raise RefusalError("only function calls are run")
        for schema in self.schemas:
            if schema.name == call.name:
                return schema
        names = ", ".join(schema.name for schema in self.schemas)
        raise RefusalError(f"there is no tool {call.name!r}; the tools are {names}")

    def _next_id(self, prefix: str) -> str:
        count = sum(1 for evidence in self._evidence if evidence.evidence_id[0] == prefix)
        return f"{prefix}{count + 1}"

    async def _call(
        self, name: str, arguments: dict[str, Any]
    ) -> tuple[SourceEvidence, dict[str, Any]]:
        if "language" not in arguments:
            # Titles and labels then come in the language of the answer, unless the model
            # asks for the other one.
            arguments = {**arguments, "language": self.language}
        called = await self.client.call_tool(name, arguments)
        if called.is_error:
            raise ToolFailureError(error_text(called))
        model = SERVER_TOOLS[name]
        try:
            result = model.model_validate_json(json.dumps(called.structured_content))
        except ValidationError as error:
            # A fault of the server or of this package, which the model cannot mend.
            logger.warning("The result of %s could not be read: %s", name, error)
            raise ToolFailureError(f"the result of {name} could not be read") from error
        evidence_id = self._next_id("E")
        self._sources[evidence_id] = result
        return SourceEvidence(evidence_id, result), source_view(evidence_id, result, self.language)

    async def _calculate(
        self, arguments: dict[str, Any]
    ) -> tuple[CalculationEvidence, dict[str, Any]]:
        reads: dict[str, str] = {}
        names: dict[str, str] = {}
        for step in cast("list[dict[str, Any]]", arguments["steps"]):
            if "snapshot" not in step:
                continue
            evidence_id = cast("str", step["snapshot"])
            snapshot_id = self._snapshot_of(evidence_id)
            reads[cast("str", step["name"])] = evidence_id
            names.setdefault(snapshot_id, evidence_id)
            step["snapshot"] = snapshot_id
        try:
            plan = Plan.model_validate_json(json.dumps(arguments, default=str))
        except ValidationError as error:
            reasons = "; ".join(
                f"{located(problem['loc'], problem['msg'])}" for problem in error.errors()
            )
            raise RefusalError(f"the plan is not valid: {reasons}") from error
        try:
            # Abandoned when the run's time is up; the thread then finishes on its own.
            result = await anyio.to_thread.run_sync(self.engine.run, plan, abandon_on_cancel=True)
        except CalculationError as error:
            message = str(error)
            for snapshot_id, evidence_id in names.items():
                if isinstance(error.__cause__, SnapshotError) and snapshot_id in message:
                    # The store's reason can name a local path, which stays here.
                    message = (
                        f"Step {error.step!r} reads the snapshot of {evidence_id}, which "
                        "cannot be read"
                    )
                message = message.replace(snapshot_id, evidence_id)
            raise ToolFailureError(message) from error
        evidence_id = self._next_id("C")
        view = calculation_view(evidence_id, result, reads, self.language)
        return CalculationEvidence(evidence_id, result), view

    def _snapshot_of(self, evidence_id: str) -> str:
        result = self._sources.get(evidence_id)
        if result is None:
            raise ToolFailureError(f"{evidence_id} is not a result of this run")
        if not isinstance(result, QueryResult):
            raise ToolFailureError(f"{evidence_id} is not a query_table result, so it has no cells")
        snapshot_id = result.provenance.response_sha256
        if not self.store.path(snapshot_id).is_dir():
            raise ToolFailureError(f"{evidence_id} has no snapshot, so calculate cannot read it")
        return snapshot_id


__all__ = ["CALCULATE", "SERVER_TOOLS", "ToolOutcome", "Toolbox", "calculate_schema"]

"""The evidence gate: every number in an answer against the results it cites (spec 0003)."""

from opencited.gate.check import (
    CalculationEvidence,
    Evidence,
    Finding,
    Match,
    Report,
    SourceEvidence,
    check,
    cited_ids,
)
from opencited.gate.numbers import Number, normalise, read_numbers

__all__ = [
    "CalculationEvidence",
    "Evidence",
    "Finding",
    "Match",
    "Number",
    "Report",
    "SourceEvidence",
    "check",
    "cited_ids",
    "normalise",
    "read_numbers",
]

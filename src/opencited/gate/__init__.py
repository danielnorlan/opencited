"""The evidence gate: every number in an answer against the results it cites (spec 0003)."""

from opencited.gate.check import (
    CalculationEvidence,
    Evidence,
    Finding,
    Report,
    SourceEvidence,
    check,
)
from opencited.gate.numbers import Number, normalise, read_numbers

__all__ = [
    "CalculationEvidence",
    "Evidence",
    "Finding",
    "Number",
    "Report",
    "SourceEvidence",
    "check",
    "normalise",
    "read_numbers",
]

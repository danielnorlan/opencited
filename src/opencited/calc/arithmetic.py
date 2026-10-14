"""Exact arithmetic on rational numbers, rounded once where a plan asks for it (ADR 0011).

DuckDB returns floating point for division and means of decimals, so the engine lets
DuckDB select cells and computes here, on ``Fraction`` values made from the cells'
``Decimal`` values without any loss. A result is turned back into a decimal either
exactly, when it has a finite decimal expansion, or by rounding once with ties away
from zero.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from decimal import Decimal
from fractions import Fraction

# DuckDB's widest decimal type holds 38 digits, and so may a result.
MAX_SIGNIFICANT = 38
HUNDRED = Fraction(100)


def total(values: Iterable[Decimal]) -> Fraction:
    return sum((Fraction(value) for value in values), Fraction(0))


def mean(values: Iterable[Decimal]) -> Fraction:
    numbers = [Fraction(value) for value in values]
    if not numbers:
        raise ValueError("a mean needs at least one value")
    return sum(numbers, Fraction(0)) / len(numbers)


def quotient(left: Fraction, right: Fraction) -> Fraction:
    if right == 0:
        raise ZeroDivisionError("division by zero")
    return left / right


def change_percent(old: Fraction, new: Fraction) -> Fraction:
    return quotient(new - old, old) * HUNDRED


def share_percent(part: Fraction, whole: Fraction) -> Fraction:
    return quotient(part, whole) * HUNDRED


def round_away(value: Fraction, decimals: int) -> Decimal:
    """Return ``value`` rounded to ``decimals`` places, with ties away from zero."""
    scaled = abs(value) * 10**decimals
    whole = math.floor(scaled + Fraction(1, 2))
    sign = "-" if value < 0 and whole != 0 else ""
    return Decimal(f"{sign}{whole}E-{decimals}")


def exact_decimal(value: Fraction) -> Decimal | None:
    """Return ``value`` as a decimal when its expansion ends, or None when it repeats."""
    rest = value.denominator
    twos = fives = 0
    while rest % 2 == 0:
        rest //= 2
        twos += 1
    while rest % 5 == 0:
        rest //= 5
        fives += 1
    if rest != 1:
        return None
    places = max(twos, fives)
    return Decimal(f"{value.numerator * (10**places // value.denominator)}E-{places}")


def significant(value: Decimal) -> int:
    """Return the significant digits of a decimal as written, at its own scale."""
    return 0 if value.is_zero() else len(value.as_tuple().digits)


def fraction_text(value: Fraction) -> str:
    """Return a fraction as ``numerator/denominator``, or the integer alone."""
    if value.denominator == 1:
        return str(value.numerator)
    return f"{value.numerator}/{value.denominator}"

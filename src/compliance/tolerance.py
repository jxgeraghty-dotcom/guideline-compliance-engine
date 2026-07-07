"""Numeric tolerance for limit comparisons.

Weights and durations are floats, so a holding sitting *exactly* on a limit
(e.g. precisely 5.00%) can flip a naive ``value > limit`` test on floating-point
noise. A limit engine must be deterministic at the boundary, so every rule
compares through these helpers with a small, explicit and documented epsilon:
a value only *exceeds* a limit once it is past it by more than ``TOLERANCE``.

``TOLERANCE`` is 1e-9 in weight/duration terms — i.e. one-ten-millionth of a
percent, or ~0.03 basis points of a year — far below any economically
meaningful threshold, but comfortably above float rounding error.
"""

from __future__ import annotations

TOLERANCE = 1e-9


def exceeds(value: float, limit: float, tol: float = TOLERANCE) -> bool:
    """True if ``value`` is above ``limit`` by more than ``tol`` (a real breach)."""
    return value > limit + tol


def below(value: float, limit: float, tol: float = TOLERANCE) -> bool:
    """True if ``value`` is below ``limit`` by more than ``tol`` (a real shortfall)."""
    return value < limit - tol


def at_least(value: float, threshold: float, tol: float = TOLERANCE) -> bool:
    """True if ``value`` has reached ``threshold`` (within ``tol``)."""
    return value >= threshold - tol

"""Core domain model: positions, portfolios and the severity scale.

These types are deliberately plain dataclasses with no dependency on the rules
engine, so they can be reused by loaders, rules, reports and tests alike.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Callable, Iterable


class Severity(IntEnum):
    """Ordered severity scale for findings and rule results.

    Ordering matters: ``max()`` over a set of severities yields the most
    serious one, which is how a rule rolls its findings up into a single
    verdict and how the report rolls rules up into an overall status.
    """

    PASS = 0      # within guideline limits
    INFO = 1      # informational metric, no action required
    WARN = 2      # inside limits but approaching them, or a data-quality gap
    BREACH = 3    # guideline limit exceeded

    @property
    def label(self) -> str:
        return self.name

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.name


@dataclass
class Position:
    """A single holding in a portfolio.

    ``market_value`` is expressed in the portfolio's base currency; currency
    conversion is assumed to have happened upstream. ``rating`` and
    ``duration`` are optional because not every asset class carries them
    (e.g. equities have no credit rating).
    """

    security_id: str
    issuer: str
    market_value: float
    sector: str = "Unclassified"
    asset_class: str = "Fixed Income"
    rating: str | None = None
    duration: float | None = None
    currency: str = "USD"

    def __post_init__(self) -> None:
        if self.market_value < 0:
            raise ValueError(
                f"Position {self.security_id!r} has negative market value "
                f"{self.market_value}; short positions are not supported."
            )


@dataclass
class Portfolio:
    """A collection of positions plus identifying metadata."""

    name: str
    positions: list[Position] = field(default_factory=list)
    base_currency: str = "USD"
    as_of: str | None = None

    @property
    def total_market_value(self) -> float:
        return sum(p.market_value for p in self.positions)

    @property
    def position_count(self) -> int:
        return len(self.positions)

    def weight(self, position: Position) -> float:
        """Fraction of the portfolio held in ``position`` (0.0 if empty)."""
        total = self.total_market_value
        return position.market_value / total if total else 0.0

    def aggregate_market_value(self, key: Callable[[Position], str]) -> dict[str, float]:
        """Sum market value grouped by an arbitrary key function."""
        buckets: dict[str, float] = defaultdict(float)
        for p in self.positions:
            buckets[key(p)] += p.market_value
        return dict(buckets)

    def aggregate_weight(self, key: Callable[[Position], str]) -> dict[str, float]:
        """Portfolio weight grouped by an arbitrary key function."""
        total = self.total_market_value
        if not total:
            return {}
        return {k: mv / total for k, mv in self.aggregate_market_value(key).items()}

    def positions_by(self, key: Callable[[Position], str]) -> dict[str, list[Position]]:
        """Group positions into lists keyed by ``key``."""
        buckets: dict[str, list[Position]] = defaultdict(list)
        for p in self.positions:
            buckets[key(p)].append(p)
        return dict(buckets)

    def weighted_average(
        self,
        value: Callable[[Position], float | None],
        *,
        over: Iterable[Position] | None = None,
    ) -> float:
        """Market-value-weighted average of ``value`` across positions.

        Positions whose ``value`` is ``None`` are excluded from both the
        numerator and denominator, so the result is the weighted average over
        the positions that actually carry the attribute. Weights are taken
        relative to the *whole* portfolio, matching how metrics such as
        effective duration are conventionally reported.
        """
        total = self.total_market_value
        if not total:
            return 0.0
        positions = list(over) if over is not None else self.positions
        acc = 0.0
        for p in positions:
            v = value(p)
            if v is None:
                continue
            acc += (p.market_value / total) * v
        return acc

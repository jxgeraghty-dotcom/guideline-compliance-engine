"""Core domain model: positions, portfolios and the severity scale.

These types are deliberately plain dataclasses with no dependency on the rules
engine, so they can be reused by loaders, rules, reports and tests alike.

Two economic concepts underpin the guideline rules:

* **Base value** — a position's mark-to-market converted into the portfolio's
  base currency via :attr:`Portfolio.fx_rates`. Portfolio weights and NAV are
  always expressed in base-currency terms.
* **Exposure** — a position's *economic* exposure. For cash instruments this
  equals market value; for a derivative it is the (signed) notional, so a
  small-mark, large-notional swap or CDS contributes its true risk when a rule
  runs with look-through enabled.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Callable, Iterable

#: Instrument types treated as derivatives for look-through and sign handling.
DERIVATIVE_TYPES = frozenset(
    {"future", "forward", "swap", "irs", "trs", "cds", "option", "swaption"}
)


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

    ``market_value`` is expressed in the position's own ``currency``; the
    portfolio converts it to base currency when computing weights. ``rating``
    and ``duration`` are optional because not every asset class carries them.

    Derivative fields enable *look-through*: ``notional`` is the economic
    exposure, and ``underlying_issuer`` / ``underlying_sector`` attribute that
    exposure to the reference name/sector (e.g. a single-name CDS). ``notional``
    may be negative to represent a short/hedge (e.g. protection bought).
    ``ultimate_parent`` supports rolling several issuing entities up to one
    parent for aggregate concentration limits.
    """

    security_id: str
    issuer: str
    market_value: float
    sector: str = "Unclassified"
    asset_class: str = "Fixed Income"
    rating: str | None = None
    duration: float | None = None
    currency: str = "USD"
    ultimate_parent: str | None = None
    instrument_type: str = "bond"
    notional: float | None = None
    underlying_issuer: str | None = None
    underlying_sector: str | None = None

    def __post_init__(self) -> None:
        # Cash instruments are long-only in this model; derivatives may carry a
        # negative mark (a swap that is a liability) so are exempt from the check.
        if self.market_value < 0 and not self.is_derivative:
            raise ValueError(
                f"Position {self.security_id!r} has negative market value "
                f"{self.market_value}; short cash positions are not supported."
            )

    @property
    def is_derivative(self) -> bool:
        return self.instrument_type.lower() in DERIVATIVE_TYPES

    @property
    def parent(self) -> str:
        """Ultimate parent for aggregation; falls back to the issuer."""
        return self.ultimate_parent or self.issuer

    @property
    def risk_issuer(self) -> str:
        """Issuer that bears the economic risk (reference entity for a CDS)."""
        return self.underlying_issuer or self.issuer

    @property
    def risk_sector(self) -> str:
        """Sector that bears the economic risk (underlying for an overlay)."""
        return self.underlying_sector or self.sector

    def local_exposure(self) -> float:
        """Economic exposure in the position's own currency.

        Notional for a derivative (its true risk), market value otherwise.
        """
        if self.is_derivative and self.notional is not None:
            return self.notional
        return self.market_value


class FxError(Exception):
    """Raised when a currency cannot be converted for lack of an FX rate."""


@dataclass
class Portfolio:
    """A collection of positions plus identifying metadata.

    ``fx_rates`` maps a currency code to the value of one unit of that currency
    in the base currency (e.g. ``{"EUR": 1.08}`` means 1 EUR = 1.08 USD when the
    base is USD). The base currency itself is always 1.0 and need not be listed.
    """

    name: str
    positions: list[Position] = field(default_factory=list)
    base_currency: str = "USD"
    as_of: str | None = None
    fx_rates: dict[str, float] = field(default_factory=dict)

    # ----- FX / valuation -------------------------------------------------- #

    def rate(self, currency: str) -> float | None:
        """FX rate from ``currency`` to base, or ``None`` if unavailable."""
        ccy = (currency or self.base_currency).upper()
        if ccy == self.base_currency.upper():
            return 1.0
        return self.fx_rates.get(ccy)

    def to_base(self, value: float, currency: str) -> float:
        rate = self.rate(currency)
        if rate is None:
            raise FxError(
                f"No FX rate for {currency!r} -> {self.base_currency}; "
                f"add it to fx_rates."
            )
        return value * rate

    def base_value(self, position: Position) -> float:
        """Mark-to-market of ``position`` in base currency."""
        return self.to_base(position.market_value, position.currency)

    def base_exposure(self, position: Position) -> float:
        """Economic exposure of ``position`` in base currency."""
        return self.to_base(position.local_exposure(), position.currency)

    def missing_currencies(self) -> list[str]:
        """Currencies present in the book that have no usable FX rate."""
        missing = {
            p.currency.upper()
            for p in self.positions
            if self.rate(p.currency) is None
        }
        return sorted(missing)

    # ----- aggregates ------------------------------------------------------ #

    @property
    def nav(self) -> float:
        """Net asset value: sum of base-currency market values."""
        return sum(self.base_value(p) for p in self.positions)

    @property
    def total_market_value(self) -> float:
        """Alias for :attr:`nav`, kept for readability at call sites."""
        return self.nav

    @property
    def position_count(self) -> int:
        return len(self.positions)

    def weight(self, position: Position) -> float:
        """Base-value weight of ``position`` in the portfolio (0.0 if empty)."""
        nav = self.nav
        return self.base_value(position) / nav if nav else 0.0

    def aggregate(
        self,
        key: Callable[[Position], str],
        value: Callable[[Position], float],
    ) -> dict[str, float]:
        """Sum an arbitrary ``value`` grouped by an arbitrary ``key``."""
        buckets: dict[str, float] = defaultdict(float)
        for p in self.positions:
            buckets[key(p)] += value(p)
        return dict(buckets)

    def aggregate_market_value(self, key: Callable[[Position], str]) -> dict[str, float]:
        """Base-value totals grouped by ``key``."""
        return self.aggregate(key, self.base_value)

    def aggregate_weight(
        self,
        key: Callable[[Position], str],
        value: Callable[[Position], float] | None = None,
    ) -> dict[str, float]:
        """Portfolio weight grouped by ``key`` (base value unless ``value`` given).

        Pass ``value=portfolio.base_exposure`` to weight by economic exposure
        (look-through). The denominator is always NAV.
        """
        nav = self.nav
        if not nav:
            return {}
        value = value or self.base_value
        return {k: v / nav for k, v in self.aggregate(key, value).items()}

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
        weight: Callable[[Position], float] | None = None,
        over: Iterable[Position] | None = None,
    ) -> float:
        """NAV-weighted average of ``value`` across positions.

        Positions whose ``value`` is ``None`` are excluded. ``weight`` selects
        the weighting basis (base value by default; pass ``base_exposure`` for
        look-through). Weights are always taken relative to NAV, matching how
        metrics such as effective duration are conventionally reported.
        """
        nav = self.nav
        if not nav:
            return 0.0
        weight = weight or self.base_value
        positions = list(over) if over is not None else self.positions
        acc = 0.0
        for p in positions:
            v = value(p)
            if v is None:
                continue
            acc += (weight(p) / nav) * v
        return acc

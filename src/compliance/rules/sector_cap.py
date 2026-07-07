"""Sector-cap rule.

Caps the portfolio weight in any one sector, with optional per-sector
overrides (e.g. a tighter cap on Financials) and optional per-sector floors
(minimum weights, e.g. a mandate to hold at least X% government). With
``look_through`` enabled, a derivative's notional is attributed to its
underlying sector rather than its (small) market value.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from compliance.models import Portfolio, Position, Severity
from compliance.rules.base import Finding, Rule, RuleResult, pct, register_rule
from compliance.tolerance import at_least, below, exceeds

_NETTING = {"net", "gross"}


@register_rule
class SectorCapRule(Rule):
    """Flag sectors that breach a maximum (or fall below a minimum) weight.

    Config keys:
        max_weight (float, required): default sector cap as a fraction.
        overrides (dict, optional): ``{sector: cap}`` per-sector maxima.
        floors (dict, optional): ``{sector: min}`` per-sector minima.
        warn_ratio (float, optional): warn when weight reaches this fraction of
            the cap; default ``0.9``.
        look_through (bool, optional): attribute derivative notional to the
            underlying sector; default ``false``.
        netting (str, optional): ``net`` (default) or ``gross``; see the issuer
            rule. Only affects signed derivatives under look-through.
    """

    rule_type = "sector_cap"
    config_keys = frozenset(
        {"max_weight", "overrides", "floors", "warn_ratio", "look_through", "netting"}
    )

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.max_weight = self._require_number("max_weight")
        self.overrides: dict[str, float] = dict(config.get("overrides") or {})
        self.floors: dict[str, float] = dict(config.get("floors") or {})
        self.warn_ratio = self._get_number("warn_ratio", 0.9) or 0.9
        self.look_through = bool(config.get("look_through", False))
        self.netting = str(config.get("netting", "net")).lower()
        if self.netting not in _NETTING:
            raise ValueError(
                f"Rule {self.rule_id!r}: 'netting' must be one of {sorted(_NETTING)}, "
                f"got {config.get('netting')!r}."
            )

    def evaluate(self, portfolio: Portfolio) -> RuleResult:
        key: Callable[[Position], str] = (
            (lambda p: p.risk_sector) if self.look_through else (lambda p: p.sector)
        )
        value: Callable[[Position], float] | None
        if not self.look_through:
            value = None
        elif self.netting == "gross":
            value = lambda p: abs(portfolio.base_exposure(p))  # noqa: E731
        else:
            value = portfolio.base_exposure
        weights = portfolio.aggregate_weight(key, value)
        findings: list[Finding] = []

        for sector, weight in sorted(weights.items(), key=lambda kv: kv[1], reverse=True):
            cap = self.overrides.get(sector, self.max_weight)
            if exceeds(weight, cap):
                findings.append(
                    Finding(
                        subject=sector,
                        message=(
                            f"{sector} is {pct(weight)} of the portfolio, over the "
                            f"{pct(cap)} sector cap (+{pct(weight - cap)})."
                        ),
                        severity=Severity.BREACH,
                        observed=weight,
                        limit=cap,
                        metric="weight",
                    )
                )
            elif at_least(weight, self.warn_ratio * cap):
                findings.append(
                    Finding(
                        subject=sector,
                        message=(
                            f"{sector} is {pct(weight)} of the portfolio, approaching "
                            f"the {pct(cap)} sector cap."
                        ),
                        severity=Severity.WARN,
                        observed=weight,
                        limit=cap,
                        metric="weight",
                    )
                )

        # Per-sector minimum weights (mandate floors).
        for sector, floor in self.floors.items():
            weight = weights.get(sector, 0.0)
            if below(weight, floor):
                findings.append(
                    Finding(
                        subject=sector,
                        message=(
                            f"{sector} is {pct(weight)} of the portfolio, below the "
                            f"{pct(floor)} minimum ({pct(floor - weight)} short)."
                        ),
                        severity=Severity.BREACH,
                        observed=weight,
                        limit=floor,
                        metric="weight",
                    )
                )

        metrics = {
            "sector_count": len(weights),
            "default_cap": self.max_weight,
            "look_through": self.look_through,
            "netting": self.netting,
            "sector_weights": dict(
                sorted(weights.items(), key=lambda kv: kv[1], reverse=True)
            ),
        }
        return self._new_result(findings, metrics)

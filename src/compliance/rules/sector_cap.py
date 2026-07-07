"""Sector-cap rule.

Caps the portfolio weight in any one sector, with optional per-sector
overrides (e.g. a tighter cap on Financials) and optional per-sector floors
(minimum weights, e.g. a mandate to hold at least X% government).
"""

from __future__ import annotations

from typing import Any

from compliance.models import Portfolio, Severity
from compliance.rules.base import Finding, Rule, RuleResult, register_rule


@register_rule
class SectorCapRule(Rule):
    """Flag sectors that breach a maximum (or fall below a minimum) weight.

    Config keys:
        max_weight (float, required): default sector cap as a fraction.
        overrides (dict, optional): ``{sector: cap}`` per-sector maxima.
        floors (dict, optional): ``{sector: min}`` per-sector minima.
        warn_ratio (float, optional): warn when weight reaches this fraction of
            the cap; default ``0.9``.
    """

    rule_type = "sector_cap"

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.max_weight = self._require_number("max_weight")
        self.overrides: dict[str, float] = dict(config.get("overrides") or {})
        self.floors: dict[str, float] = dict(config.get("floors") or {})
        self.warn_ratio = self._get_number("warn_ratio", 0.9) or 0.9

    def evaluate(self, portfolio: Portfolio) -> RuleResult:
        weights = portfolio.aggregate_weight(lambda p: p.sector)
        findings: list[Finding] = []

        for sector, weight in sorted(weights.items(), key=lambda kv: kv[1], reverse=True):
            cap = self.overrides.get(sector, self.max_weight)
            if weight > cap:
                findings.append(
                    Finding(
                        subject=sector,
                        message=(
                            f"{sector} is {_pct(weight)} of the portfolio, over the "
                            f"{_pct(cap)} sector cap (+{_pct(weight - cap)})."
                        ),
                        severity=Severity.BREACH,
                        observed=weight,
                        limit=cap,
                        metric="weight",
                    )
                )
            elif weight >= self.warn_ratio * cap:
                findings.append(
                    Finding(
                        subject=sector,
                        message=(
                            f"{sector} is {_pct(weight)} of the portfolio, approaching "
                            f"the {_pct(cap)} sector cap."
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
            if weight < floor:
                findings.append(
                    Finding(
                        subject=sector,
                        message=(
                            f"{sector} is {_pct(weight)} of the portfolio, below the "
                            f"{_pct(floor)} minimum ({_pct(floor - weight)} short)."
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
            "sector_weights": dict(
                sorted(weights.items(), key=lambda kv: kv[1], reverse=True)
            ),
        }
        return self._new_result(findings, metrics)


def _pct(value: float) -> str:
    return f"{value * 100:.2f}%"

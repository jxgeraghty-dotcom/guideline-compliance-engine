"""Duration-band rule.

Requires the portfolio's market-value-weighted effective duration to sit
within a ``[min_duration, max_duration]`` band — typically the benchmark
duration plus or minus a tolerance. An optional ``warn_buffer`` raises a
warning as the portfolio drifts toward either edge of the band.
"""

from __future__ import annotations

from typing import Any

from compliance.models import Portfolio, Severity
from compliance.rules.base import Finding, Rule, RuleResult, register_rule
from compliance.tolerance import below, exceeds


@register_rule
class DurationBandRule(Rule):
    """Check portfolio effective duration against a band.

    Config keys:
        min_duration (float, required): lower edge, in years.
        max_duration (float, required): upper edge, in years.
        warn_buffer (float, optional): warn when within this many years of an
            edge; default ``0.0`` (no warning zone).
        look_through (bool, optional): weight duration by economic exposure
            (notional) so rate overlays such as bond futures and swaps count
            their true contribution; default ``false``.
    """

    rule_type = "duration_band"

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.min_duration = self._require_number("min_duration")
        self.max_duration = self._require_number("max_duration")
        if self.min_duration > self.max_duration:
            raise ValueError(
                f"Rule {self.rule_id!r}: min_duration ({self.min_duration}) must not "
                f"exceed max_duration ({self.max_duration})."
            )
        self.warn_buffer = self._get_number("warn_buffer", 0.0) or 0.0
        self.look_through = bool(config.get("look_through", False))

    def evaluate(self, portfolio: Portfolio) -> RuleResult:
        basis = portfolio.base_exposure if self.look_through else portfolio.base_value
        duration = portfolio.weighted_average(lambda p: p.duration, weight=basis)
        findings: list[Finding] = []
        nav = portfolio.nav

        # Data-quality flag: fixed-income holdings missing a duration understate
        # the portfolio figure, so surface any material gap.
        missing = [
            p for p in portfolio.positions
            if p.duration is None and p.asset_class.lower().startswith("fixed")
        ]
        missing_weight = sum(basis(p) for p in missing) / nav if nav else 0.0
        if missing_weight > 0:
            findings.append(
                Finding(
                    subject="duration coverage",
                    message=(
                        f"{_pct(missing_weight)} of fixed-income holdings have no "
                        f"duration; reported duration may be understated."
                    ),
                    severity=Severity.WARN,
                    observed=missing_weight,
                    metric="weight",
                    category="DATA",
                )
            )

        band = f"{self.min_duration:.2f}-{self.max_duration:.2f} yrs"
        if below(duration, self.min_duration) or exceeds(duration, self.max_duration):
            side = "below" if duration < self.min_duration else "above"
            edge = self.min_duration if side == "below" else self.max_duration
            findings.append(
                Finding(
                    subject="portfolio duration",
                    message=(
                        f"Effective duration {duration:.2f} yrs is {side} the "
                        f"{band} band ({abs(duration - edge):.2f} yrs {side} the edge)."
                    ),
                    severity=Severity.BREACH,
                    observed=duration,
                    limit=edge,
                    metric="years",
                )
            )
        elif self.warn_buffer > 0 and (
            duration - self.min_duration < self.warn_buffer
            or self.max_duration - duration < self.warn_buffer
        ):
            near = self.min_duration if (
                duration - self.min_duration < self.max_duration - duration
            ) else self.max_duration
            findings.append(
                Finding(
                    subject="portfolio duration",
                    message=(
                        f"Effective duration {duration:.2f} yrs is within "
                        f"{self.warn_buffer:.2f} yrs of the {band} band edge."
                    ),
                    severity=Severity.WARN,
                    observed=duration,
                    limit=near,
                    metric="years",
                )
            )

        metrics = {
            "portfolio_duration": duration,
            "min_duration": self.min_duration,
            "max_duration": self.max_duration,
            "look_through": self.look_through,
        }
        return self._new_result(findings, metrics)


def _pct(value: float) -> str:
    return f"{value * 100:.2f}%"

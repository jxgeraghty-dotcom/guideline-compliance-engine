"""Issuer-concentration rule.

Caps the portfolio weight held in any single issuer — the classic "no more
than 5% in one name" IMA guideline. Supports per-issuer overrides (e.g. a
higher limit for a specific name) and sector/issuer exemptions (e.g. sovereign
debt often carries no issuer cap).
"""

from __future__ import annotations

from typing import Any

from compliance.models import Portfolio, Severity
from compliance.rules.base import Finding, Rule, RuleResult, register_rule


@register_rule
class IssuerConcentrationRule(Rule):
    """Flag issuers whose aggregate weight exceeds a limit.

    Config keys:
        max_weight (float, required): default cap as a fraction, e.g. ``0.05``.
        warn_at (float, optional): warn threshold; defaults to 90% of the cap.
        overrides (dict, optional): ``{issuer: cap}`` per-issuer limits.
        exempt_issuers (list, optional): issuers excluded from the check.
        exempt_sectors (list, optional): sectors whose issuers are excluded.
    """

    rule_type = "issuer_concentration"

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.max_weight = self._require_number("max_weight")
        self.warn_at = self._get_number("warn_at", 0.9 * self.max_weight)
        self.overrides: dict[str, float] = dict(config.get("overrides") or {})
        self.exempt_issuers = {str(i) for i in (config.get("exempt_issuers") or [])}
        self.exempt_sectors = {str(s) for s in (config.get("exempt_sectors") or [])}

    def _is_exempt(self, portfolio: Portfolio, issuer: str) -> bool:
        if issuer in self.exempt_issuers:
            return True
        if not self.exempt_sectors:
            return False
        # Exempt only if *every* position for the issuer is in an exempt sector.
        issuer_positions = [p for p in portfolio.positions if p.issuer == issuer]
        return bool(issuer_positions) and all(
            p.sector in self.exempt_sectors for p in issuer_positions
        )

    def evaluate(self, portfolio: Portfolio) -> RuleResult:
        weights = portfolio.aggregate_weight(lambda p: p.issuer)
        findings: list[Finding] = []
        largest_issuer: str | None = None
        largest_weight = 0.0

        for issuer, weight in sorted(weights.items(), key=lambda kv: kv[1], reverse=True):
            if self._is_exempt(portfolio, issuer):
                continue
            if weight > largest_weight:
                largest_issuer, largest_weight = issuer, weight

            limit = self.overrides.get(issuer, self.max_weight)
            if weight > limit:
                findings.append(
                    Finding(
                        subject=issuer,
                        message=(
                            f"{issuer} is {_pct(weight)} of the portfolio, "
                            f"over the {_pct(limit)} issuer limit "
                            f"(+{_pct(weight - limit)})."
                        ),
                        severity=Severity.BREACH,
                        observed=weight,
                        limit=limit,
                        metric="weight",
                    )
                )
            elif weight >= min(self.warn_at, limit):
                findings.append(
                    Finding(
                        subject=issuer,
                        message=(
                            f"{issuer} is {_pct(weight)} of the portfolio, "
                            f"approaching the {_pct(limit)} issuer limit."
                        ),
                        severity=Severity.WARN,
                        observed=weight,
                        limit=limit,
                        metric="weight",
                    )
                )

        metrics = {
            "issuer_count": len(weights),
            "largest_issuer": largest_issuer,
            "largest_issuer_weight": largest_weight,
            "limit": self.max_weight,
        }
        return self._new_result(findings, metrics)


def _pct(value: float) -> str:
    return f"{value * 100:.2f}%"

"""Currency-exposure rule.

Caps exposure to non-base currencies — a common IMA control on an otherwise
domestic mandate ("no more than 10% in any single foreign currency, 25% in
aggregate"). Weights are computed in base-currency terms, so the portfolio's
:attr:`~compliance.models.Portfolio.fx_rates` must cover every currency held;
the engine validates that up front.
"""

from __future__ import annotations

from typing import Any

from compliance.models import Portfolio, Severity
from compliance.rules.base import Finding, Rule, RuleResult, register_rule
from compliance.tolerance import at_least, exceeds


@register_rule
class CurrencyExposureRule(Rule):
    """Flag foreign-currency exposures over per-currency or aggregate caps.

    Config keys:
        max_per_currency (float, required): cap on any single non-base currency.
        overrides (dict, optional): ``{currency: cap}`` per-currency limits.
        max_aggregate_foreign (float, optional): cap on total non-base exposure.
        warn_ratio (float, optional): warn at this fraction of a cap; default
            ``0.9``.
    """

    rule_type = "currency_exposure"
    config_keys = frozenset(
        {"max_per_currency", "overrides", "max_aggregate_foreign", "warn_ratio"}
    )

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.max_per_currency = self._require_number("max_per_currency")
        self.overrides: dict[str, float] = {
            str(k).upper(): float(v) for k, v in (config.get("overrides") or {}).items()
        }
        self.max_aggregate_foreign = self._get_number("max_aggregate_foreign")
        self.warn_ratio = self._get_number("warn_ratio", 0.9) or 0.9

    def evaluate(self, portfolio: Portfolio) -> RuleResult:
        base = portfolio.base_currency.upper()
        weights = portfolio.aggregate_weight(lambda p: p.currency.upper())
        findings: list[Finding] = []
        foreign_weight = 0.0

        for ccy, weight in sorted(weights.items(), key=lambda kv: kv[1], reverse=True):
            if ccy == base:
                continue
            foreign_weight += weight
            cap = self.overrides.get(ccy, self.max_per_currency)
            if exceeds(weight, cap):
                findings.append(
                    Finding(
                        subject=ccy,
                        message=(
                            f"{ccy} exposure is {_pct(weight)}, over the {_pct(cap)} "
                            f"per-currency limit (+{_pct(weight - cap)})."
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
                        subject=ccy,
                        message=(
                            f"{ccy} exposure is {_pct(weight)}, approaching the "
                            f"{_pct(cap)} per-currency limit."
                        ),
                        severity=Severity.WARN,
                        observed=weight,
                        limit=cap,
                        metric="weight",
                    )
                )

        if self.max_aggregate_foreign is not None:
            findings.append(self._aggregate_finding(foreign_weight))

        findings = [f for f in findings if f.severity >= Severity.WARN]
        metrics = {
            "base_currency": base,
            "foreign_weight": foreign_weight,
            "max_per_currency": self.max_per_currency,
            "currency_weights": dict(
                sorted(weights.items(), key=lambda kv: kv[1], reverse=True)
            ),
        }
        return self._new_result(findings, metrics)

    def _aggregate_finding(self, foreign_weight: float) -> Finding:
        cap = self.max_aggregate_foreign
        assert cap is not None
        if exceeds(foreign_weight, cap):
            return Finding(
                subject="foreign currency (aggregate)",
                message=(
                    f"Aggregate non-base exposure is {_pct(foreign_weight)}, over the "
                    f"{_pct(cap)} limit (+{_pct(foreign_weight - cap)})."
                ),
                severity=Severity.BREACH,
                observed=foreign_weight,
                limit=cap,
                metric="weight",
            )
        if at_least(foreign_weight, self.warn_ratio * cap):
            return Finding(
                subject="foreign currency (aggregate)",
                message=(
                    f"Aggregate non-base exposure is {_pct(foreign_weight)}, approaching "
                    f"the {_pct(cap)} limit."
                ),
                severity=Severity.WARN,
                observed=foreign_weight,
                limit=cap,
                metric="weight",
            )
        return Finding(
            subject="foreign currency (aggregate)",
            message=f"Aggregate non-base exposure is {_pct(foreign_weight)}.",
            severity=Severity.PASS,
            observed=foreign_weight,
            limit=cap,
            metric="weight",
        )


def _pct(value: float) -> str:
    return f"{value * 100:.2f}%"

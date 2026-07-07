"""Currency-exposure rule.

Caps exposure to non-base currencies — a common IMA control on an otherwise
domestic mandate ("no more than 10% in any single foreign currency, 25% in
aggregate"). Weights are computed in base-currency terms, so the portfolio's
:attr:`~compliance.models.Portfolio.fx_rates` must cover every currency held;
the engine validates that up front.

With ``look_through`` enabled the rule weighs positions by *economic* exposure
(signed notional for derivatives) instead of market value, so FX hedges count —
the "hedged exposure" basis most IMAs allow. Book a hedge as one position per
foreign leg: selling EUR forward against the base is a position with
``currency: EUR``, ``instrument_type: forward`` and a negative ``notional``
(the base-currency leg need not be booked). An over-hedged currency nets short;
a short is still exposure, so limits are tested on the magnitude of the net.
``netting: gross`` disables the offset for mandates that cap gross exposure.
"""

from __future__ import annotations

from typing import Any

from compliance.models import Portfolio, Position, Severity
from compliance.rules.base import Finding, Rule, RuleResult, pct, register_rule
from compliance.tolerance import at_least, exceeds

_NETTING = {"net", "gross"}


@register_rule
class CurrencyExposureRule(Rule):
    """Flag foreign-currency exposures over per-currency or aggregate caps.

    Config keys:
        max_per_currency (float, required): cap on any single non-base currency.
        overrides (dict, optional): ``{currency: cap}`` per-currency limits.
        max_aggregate_foreign (float, optional): cap on total non-base exposure.
        warn_ratio (float, optional): warn at this fraction of a cap; default
            ``0.9``.
        look_through (bool, optional): weight by economic exposure (signed
            notional) so FX forwards hedge measured exposure down; default
            ``false`` (market value, hedges ignored).
        netting (str, optional): ``net`` (default) lets a short hedge offset a
            long in the same currency; ``gross`` sums absolute exposures.
            Only meaningful with ``look_through``.
    """

    rule_type = "currency_exposure"
    config_keys = frozenset(
        {"max_per_currency", "overrides", "max_aggregate_foreign", "warn_ratio",
         "look_through", "netting"}
    )

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.max_per_currency = self._require_number("max_per_currency")
        self.overrides: dict[str, float] = {
            str(k).upper(): float(v) for k, v in (config.get("overrides") or {}).items()
        }
        self.max_aggregate_foreign = self._get_number("max_aggregate_foreign")
        self.warn_ratio = self._get_number("warn_ratio", 0.9) or 0.9
        self.look_through = bool(config.get("look_through", False))
        self.netting = str(config.get("netting", "net")).lower()
        if self.netting not in _NETTING:
            raise ValueError(
                f"Rule {self.rule_id!r}: 'netting' must be one of {sorted(_NETTING)}, "
                f"got {config.get('netting')!r}."
            )

    def _weights(self, portfolio: Portfolio) -> dict[str, float]:
        def key(p: Position) -> str:
            return p.currency.upper()

        if not self.look_through:
            return portfolio.aggregate_weight(key)
        if self.netting == "gross":
            return portfolio.aggregate_weight(key, lambda p: abs(portfolio.base_exposure(p)))
        return portfolio.aggregate_weight(key, portfolio.base_exposure)

    def evaluate(self, portfolio: Portfolio) -> RuleResult:
        base = portfolio.base_currency.upper()
        weights = self._weights(portfolio)
        # Under net look-through an over-hedged currency nets *short*; a short
        # is still exposure, so limits are tested on the magnitude.
        measured = {ccy: abs(w) for ccy, w in weights.items()}
        label = "net exposure" if self.look_through and self.netting == "net" else "exposure"
        findings: list[Finding] = []
        foreign_weight = 0.0

        for ccy, weight in sorted(measured.items(), key=lambda kv: kv[1], reverse=True):
            if ccy == base:
                continue
            foreign_weight += weight
            short_note = " (net short)" if weights[ccy] < 0 else ""
            cap = self.overrides.get(ccy, self.max_per_currency)
            if exceeds(weight, cap):
                findings.append(
                    Finding(
                        subject=ccy,
                        message=(
                            f"{ccy} {label} is {pct(weight)}{short_note}, over the "
                            f"{pct(cap)} per-currency limit (+{pct(weight - cap)})."
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
                            f"{ccy} {label} is {pct(weight)}{short_note}, approaching "
                            f"the {pct(cap)} per-currency limit."
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
            "look_through": self.look_through,
            "netting": self.netting,
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
                    f"Aggregate non-base exposure is {pct(foreign_weight)}, over the "
                    f"{pct(cap)} limit (+{pct(foreign_weight - cap)})."
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
                    f"Aggregate non-base exposure is {pct(foreign_weight)}, approaching "
                    f"the {pct(cap)} limit."
                ),
                severity=Severity.WARN,
                observed=foreign_weight,
                limit=cap,
                metric="weight",
            )
        return Finding(
            subject="foreign currency (aggregate)",
            message=f"Aggregate non-base exposure is {pct(foreign_weight)}.",
            severity=Severity.PASS,
            observed=foreign_weight,
            limit=cap,
            metric="weight",
        )

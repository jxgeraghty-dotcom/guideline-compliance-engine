"""Credit-quality floor rule.

Enforces a minimum credit rating. Two common IMA constructions are supported
by one rule:

* A hard floor (``max_below_weight = 0``): *no* holding may sit below the floor.
* A high-yield bucket (``max_below_weight > 0``): a limited weight is allowed
  below the floor, e.g. "up to 5% may be rated below BBB-".

Unrated holdings are treated as a data-quality flag (you cannot certify
compliance on a rating you do not have) whose severity is configurable.
"""

from __future__ import annotations

from typing import Any

from compliance import ratings
from compliance.models import Portfolio, Severity
from compliance.rules.base import Finding, Rule, RuleResult, pct, register_rule
from compliance.tolerance import at_least, exceeds

_UNRATED_SEVERITY = {
    "breach": Severity.BREACH,
    "warn": Severity.WARN,
    "ignore": Severity.INFO,
}


@register_rule
class CreditFloorRule(Rule):
    """Flag holdings below a minimum rating and police the below-floor bucket.

    Config keys:
        min_rating (str, required): floor, e.g. ``"BBB-"``.
        max_below_weight (float, optional): weight allowed below the floor;
            default ``0.0`` (any below-floor holding is a breach).
        warn_at (float, optional): warn threshold for the bucket; defaults to
            90% of ``max_below_weight``.
        treat_unrated_as (str, optional): ``"warn"`` (default), ``"breach"``
            or ``"ignore"``.
        look_through (bool, optional): weight below-floor exposure by derivative
            notional (attributed to the reference entity's rating); default
            ``false``.
        rating_basis (str, optional): how to combine multiple agency ratings —
            ``lower`` (default), ``higher`` or ``median``.
    """

    rule_type = "credit_floor"
    config_keys = frozenset(
        {"min_rating", "max_below_weight", "warn_at", "treat_unrated_as",
         "look_through", "rating_basis"}
    )

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.min_rating = str(config.get("min_rating") or "").strip()
        if ratings.notch(self.min_rating) is None:
            raise ValueError(
                f"Rule {self.rule_id!r}: 'min_rating' must be a valid rating, "
                f"got {config.get('min_rating')!r}."
            )
        self.max_below_weight = self._number("max_below_weight", 0.0)
        self.warn_at = self._number("warn_at", 0.9 * self.max_below_weight)
        self.look_through = bool(config.get("look_through", False))
        self.rating_basis = str(config.get("rating_basis", "lower")).lower()
        if self.rating_basis not in ratings.RATING_BASES:
            raise ValueError(
                f"Rule {self.rule_id!r}: 'rating_basis' must be one of "
                f"{ratings.RATING_BASES}, got {config.get('rating_basis')!r}."
            )
        unrated = str(config.get("treat_unrated_as", "warn")).lower()
        if unrated not in _UNRATED_SEVERITY:
            raise ValueError(
                f"Rule {self.rule_id!r}: 'treat_unrated_as' must be one of "
                f"{sorted(_UNRATED_SEVERITY)}, got {unrated!r}."
            )
        self.unrated_severity = _UNRATED_SEVERITY[unrated]

    def evaluate(self, portfolio: Portfolio) -> RuleResult:
        findings: list[Finding] = []
        below_weight = 0.0
        below_positions: list[str] = []
        unrated_weight = 0.0
        nav = portfolio.nav

        def weight_of(position) -> float:
            if not nav:
                return 0.0
            basis = portfolio.base_exposure if self.look_through else portfolio.base_value
            return basis(position) / nav

        for p in portfolio.positions:
            weight = weight_of(p)
            effective = p.effective_rating(self.rating_basis)
            below = ratings.is_below_floor(effective, self.min_rating)
            if below is None:
                unrated_weight += weight
                if self.unrated_severity is not Severity.INFO:
                    findings.append(
                        Finding(
                            subject=p.security_id,
                            message=(
                                f"{p.security_id} ({p.issuer}) is unrated; cannot "
                                f"verify against the {self.min_rating} floor."
                            ),
                            severity=self.unrated_severity,
                            observed=weight,
                            metric="weight",
                            category="DATA",
                        )
                    )
            elif below:
                below_weight += weight
                below_positions.append(f"{p.security_id} ({p.issuer}, {effective})")

        findings.append(self._bucket_finding(below_weight, below_positions))

        avg = ratings.weighted_average_rating(
            [(p.effective_rating(self.rating_basis), portfolio.base_value(p))
             for p in portfolio.positions]
        )
        metrics = {
            "floor": self.min_rating,
            "rating_basis": self.rating_basis,
            "below_floor_weight": below_weight,
            "below_floor_allowance": self.max_below_weight,
            "unrated_weight": unrated_weight,
            "look_through": self.look_through,
            "weighted_average_rating": avg[0] if avg else None,
        }
        # Drop the bucket finding if it was a clean PASS with an empty bucket,
        # to avoid noise; keep genuine WARN/BREACH plus any data flags.
        findings = [f for f in findings if f.severity >= Severity.WARN]
        return self._new_result(findings, metrics)

    def _bucket_finding(self, below_weight: float, positions: list[str]) -> Finding:
        allowance = self.max_below_weight
        names = ", ".join(positions)
        if exceeds(below_weight, allowance):
            if allowance <= 0:
                message = (
                    f"{pct(below_weight)} held below the {self.min_rating} floor "
                    f"(no allowance): {names}."
                )
            else:
                message = (
                    f"{pct(below_weight)} below the {self.min_rating} floor exceeds "
                    f"the {pct(allowance)} allowance (+{pct(below_weight - allowance)}): "
                    f"{names}."
                )
            return Finding(
                subject=f"below {self.min_rating}",
                message=message,
                severity=Severity.BREACH,
                observed=below_weight,
                limit=allowance,
                metric="weight",
            )
        if allowance > 0 and at_least(below_weight, min(self.warn_at, allowance)):
            return Finding(
                subject=f"below {self.min_rating}",
                message=(
                    f"{pct(below_weight)} below the {self.min_rating} floor, "
                    f"approaching the {pct(allowance)} allowance: {names}."
                ),
                severity=Severity.WARN,
                observed=below_weight,
                limit=allowance,
                metric="weight",
            )
        return Finding(
            subject=f"below {self.min_rating}",
            message=f"{pct(below_weight)} held below the {self.min_rating} floor.",
            severity=Severity.PASS,
            observed=below_weight,
            limit=allowance,
            metric="weight",
        )

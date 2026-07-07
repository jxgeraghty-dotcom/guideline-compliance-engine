"""The compliance engine: turn a set of guidelines into a report.

The engine holds an ordered list of :class:`~compliance.rules.base.Rule`
objects and, given a portfolio, evaluates each in turn into a
:class:`~compliance.report.ComplianceReport`. A rule that raises is captured as
a data-quality breach rather than being allowed to abort the whole run — a
monitoring engine should degrade gracefully and report the problem.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

# Importing the rules package registers all built-in rule types.
import compliance.rules  # noqa: F401
from compliance.models import Portfolio, Severity
from compliance.report import ComplianceReport
from compliance.rules.base import Finding, Rule, RuleResult, create_rule
from compliance.validation import reject_unknown_keys
from compliance.waivers import Waiver, apply_waivers, as_of_date

#: Recognised top-level keys of a guideline document. ``metadata`` is a
#: free-form escape hatch for arbitrary annotations.
DOCUMENT_KEYS = frozenset(
    {"guidelines", "waivers", "fx_rates", "base_currency", "portfolio_name",
     "name", "description", "metadata"}
)


def validate_guideline_document(config: dict[str, Any]) -> None:
    """Reject unknown top-level keys (e.g. ``guidlines``, ``fx_rate``)."""
    if not isinstance(config, dict):
        raise ValueError("Guideline document must be a mapping.")
    reject_unknown_keys("Guideline document", config, DOCUMENT_KEYS)


class ComplianceEngine:
    """Evaluate a portfolio against an ordered set of guideline rules."""

    def __init__(self, rules: list[Rule], waivers: list[Waiver] | None = None):
        self.rules = rules
        self.waivers = waivers or []

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> ComplianceEngine:
        """Build an engine from a parsed guideline document.

        The document is a dict with a ``guidelines`` list (each entry a rule
        config with at least a ``type``) and an optional ``waivers`` list.
        """
        validate_guideline_document(config)
        guidelines = config.get("guidelines")
        if not isinstance(guidelines, list) or not guidelines:
            raise ValueError(
                "Guideline document must contain a non-empty 'guidelines' list."
            )
        rules = [create_rule(g) for g in guidelines]
        _check_unique_ids(rules)
        waivers = _parse_waivers(config.get("waivers"), {r.rule_id for r in rules})
        return cls(rules, waivers)

    def run(self, portfolio: Portfolio) -> ComplianceReport:
        """Evaluate every rule, apply waivers, and assemble the report."""
        results: list[RuleResult] = []
        for rule in self.rules:
            results.append(self._safe_evaluate(rule, portfolio))
        apply_waivers(results, self.waivers, as_of_date(portfolio.as_of))
        return ComplianceReport(
            portfolio_name=portfolio.name,
            base_currency=portfolio.base_currency,
            as_of=portfolio.as_of,
            generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            total_market_value=portfolio.total_market_value,
            position_count=portfolio.position_count,
            results=results,
        )

    @staticmethod
    def _safe_evaluate(rule: Rule, portfolio: Portfolio) -> RuleResult:
        try:
            return rule.evaluate(portfolio)
        except Exception as exc:  # noqa: BLE001 - deliberately defensive
            return RuleResult(
                rule_id=rule.rule_id,
                rule_type=rule.rule_type,
                description=rule.description,
                findings=[
                    Finding(
                        subject=rule.rule_id,
                        message=f"Rule failed to evaluate: {exc}",
                        severity=Severity.BREACH,
                        category="ERROR",
                    )
                ],
            )


def _check_unique_ids(rules: list[Rule]) -> None:
    seen: set[str] = set()
    for rule in rules:
        if rule.rule_id in seen:
            raise ValueError(f"Duplicate guideline id: {rule.rule_id!r}.")
        seen.add(rule.rule_id)


def _parse_waivers(raw: Any, rule_ids: set[str]) -> list[Waiver]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("'waivers' must be a list of waiver mappings.")
    waivers = [Waiver.from_config(w) for w in raw]
    for waiver in waivers:
        if waiver.rule not in rule_ids:
            raise ValueError(
                f"Waiver references unknown guideline id {waiver.rule!r}; "
                f"known ids: {', '.join(sorted(rule_ids))}."
            )
    return waivers

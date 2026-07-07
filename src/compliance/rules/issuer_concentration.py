"""Issuer-concentration rule.

Caps the portfolio weight held in any single issuer — the classic "no more
than 5% in one name" IMA guideline. Supports:

* per-issuer ``overrides`` (a name with a higher limit);
* sector/issuer ``exemptions`` (sovereign debt often carries no issuer cap);
* ``level: ultimate_parent`` to aggregate issuing entities up to their parent,
  so exposure to a banking group is measured across all its issuing vehicles;
* ``look_through`` to attribute a derivative's notional to its reference
  entity rather than counting its (small) market value.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from compliance.models import Portfolio, Position, Severity
from compliance.rules.base import Finding, Rule, RuleResult, pct, register_rule
from compliance.tolerance import at_least, exceeds

_LEVELS = {"issuer", "ultimate_parent"}
_LEVEL_ALIASES = {"parent": "ultimate_parent"}
_NETTING = {"net", "gross"}


@register_rule
class IssuerConcentrationRule(Rule):
    """Flag issuers (or parents) whose aggregate weight exceeds a limit.

    Config keys:
        max_weight (float, required): default cap as a fraction, e.g. ``0.05``.
        warn_at (float, optional): warn threshold; defaults to 90% of the cap.
        overrides (dict, optional): ``{name: cap}`` per-issuer/parent limits.
        exempt_issuers (list, optional): names excluded from the check.
        exempt_sectors (list, optional): sectors whose issuers are excluded.
        level (str, optional): ``issuer`` (default) or ``ultimate_parent``.
        look_through (bool, optional): attribute derivative notional to the
            underlying issuer; default ``false``.
        netting (str, optional): ``net`` (default) lets a short/hedge offset a
            long in the same name; ``gross`` sums absolute exposures so hedges
            do not net down concentration. Only affects signed derivatives.
    """

    rule_type = "issuer_concentration"
    config_keys = frozenset(
        {"max_weight", "warn_at", "overrides", "exempt_issuers", "exempt_sectors",
         "level", "look_through", "netting"}
    )

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.max_weight = self._require_number("max_weight")
        self.warn_at = self._number("warn_at", 0.9 * self.max_weight)
        self.overrides: dict[str, float] = dict(config.get("overrides") or {})
        self.exempt_issuers = {str(i) for i in (config.get("exempt_issuers") or [])}
        self.exempt_sectors = {str(s) for s in (config.get("exempt_sectors") or [])}
        self.look_through = bool(config.get("look_through", False))
        level = str(config.get("level", "issuer")).lower()
        level = _LEVEL_ALIASES.get(level, level)
        if level not in _LEVELS:
            raise ValueError(
                f"Rule {self.rule_id!r}: 'level' must be one of {sorted(_LEVELS)}, "
                f"got {config.get('level')!r}."
            )
        self.level = level
        self.netting = str(config.get("netting", "net")).lower()
        if self.netting not in _NETTING:
            raise ValueError(
                f"Rule {self.rule_id!r}: 'netting' must be one of {sorted(_NETTING)}, "
                f"got {config.get('netting')!r}."
            )

    def _name_of(self, position: Position) -> str:
        """The concentration subject a position rolls up to."""
        if self.look_through and position.underlying_issuer:
            return position.underlying_issuer
        if self.level == "ultimate_parent":
            return position.parent
        return position.issuer

    def _is_exempt(self, name: str, constituents: list[Position]) -> bool:
        if name in self.exempt_issuers:
            return True
        if not self.exempt_sectors:
            return False
        # Exempt only if *every* constituent holding sits in an exempt sector.
        # Under look-through the exposure is attributed to the underlying, so
        # the exemption must follow the underlying's sector (a CDS referencing
        # a sovereign is sovereign risk, whatever the contract's own sector).
        if self.look_through:
            return all(p.risk_sector in self.exempt_sectors for p in constituents)
        return all(p.sector in self.exempt_sectors for p in constituents)

    def _value_fn(self, portfolio: Portfolio) -> Callable[[Position], float]:
        if not self.look_through:
            return portfolio.base_value
        if self.netting == "gross":
            return lambda p: abs(portfolio.base_exposure(p))
        return portfolio.base_exposure

    def evaluate(self, portfolio: Portfolio) -> RuleResult:
        nav = portfolio.nav
        value_fn = self._value_fn(portfolio)
        groups = portfolio.positions_by(self._name_of)

        findings: list[Finding] = []
        largest_name: str | None = None
        largest_weight = 0.0

        weights = {
            name: (sum(value_fn(p) for p in ps) / nav if nav else 0.0)
            for name, ps in groups.items()
        }

        for name, weight in sorted(weights.items(), key=lambda kv: kv[1], reverse=True):
            constituents = groups[name]
            if self._is_exempt(name, constituents):
                continue
            if weight > largest_weight:
                largest_name, largest_weight = name, weight

            rolls_up = self._rollup_note(name, constituents)
            limit = self.overrides.get(name, self.max_weight)
            if exceeds(weight, limit):
                findings.append(
                    Finding(
                        subject=name,
                        message=(
                            f"{name} is {pct(weight)} of the portfolio, over the "
                            f"{pct(limit)} issuer limit (+{pct(weight - limit)}){rolls_up}."
                        ),
                        severity=Severity.BREACH,
                        observed=weight,
                        limit=limit,
                        metric="weight",
                    )
                )
            elif at_least(weight, min(self.warn_at, limit)):
                findings.append(
                    Finding(
                        subject=name,
                        message=(
                            f"{name} is {pct(weight)} of the portfolio, approaching "
                            f"the {pct(limit)} issuer limit{rolls_up}."
                        ),
                        severity=Severity.WARN,
                        observed=weight,
                        limit=limit,
                        metric="weight",
                    )
                )

        metrics = {
            "level": self.level,
            "look_through": self.look_through,
            "netting": self.netting,
            "issuer_count": len(weights),
            "largest_issuer": largest_name,
            "largest_issuer_weight": largest_weight,
            "limit": self.max_weight,
        }
        return self._new_result(findings, metrics)

    def _rollup_note(self, name: str, constituents: list[Position]) -> str:
        """Note the constituent issuers when aggregating at parent level."""
        if self.level != "ultimate_parent":
            return ""
        issuers = sorted({p.issuer for p in constituents if p.issuer != name})
        return f" [rolls up: {', '.join(issuers)}]" if issuers else ""

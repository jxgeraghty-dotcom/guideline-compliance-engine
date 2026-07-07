"""Restricted-list / name-screening rule.

Flags any holding whose issuer, ultimate parent or derivative reference entity
appears on a restricted list (sanctions, exclusions, a client's prohibited-names
list). Names come inline via ``names`` and/or from a file (resolved by the CLI
relative to the guideline document, then inlined). Matching is exact after
case-folding and whitespace trimming, so a screening hit is unambiguous — this
is a control where a false positive is disruptive and a fuzzy match is worse.
"""

from __future__ import annotations

from typing import Any

from compliance.models import Portfolio, Position, Severity
from compliance.rules.base import Finding, Rule, RuleResult, pct, register_rule

_SEVERITIES = {"breach": Severity.BREACH, "warn": Severity.WARN}


def _norm(name: str) -> str:
    return " ".join(name.strip().casefold().split())


@register_rule
class RestrictedListRule(Rule):
    """Flag holdings that touch a restricted/prohibited name.

    Config keys:
        names (list, optional): inline restricted names.
        file (str, optional): path to a newline-delimited name list. The CLI
            resolves this relative to the guideline document and inlines it into
            ``names`` before the rule is built.
        severity (str, optional): ``breach`` (default) or ``warn``.
    """

    rule_type = "restricted_list"
    config_keys = frozenset({"names", "file", "severity"})

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        names = list(config.get("names") or [])
        if config.get("file"):
            # Should have been inlined upstream; support direct paths for
            # library use by reading relative to the current directory.
            from compliance.loaders import load_name_list

            names.extend(load_name_list(config["file"]))
        self.restricted = {_norm(n): n for n in names if str(n).strip()}
        if not self.restricted:
            raise ValueError(
                f"Rule {self.rule_id!r}: restricted_list needs a non-empty 'names' "
                f"list or 'file'."
            )
        sev = str(config.get("severity", "breach")).lower()
        if sev not in _SEVERITIES:
            raise ValueError(
                f"Rule {self.rule_id!r}: 'severity' must be one of "
                f"{sorted(_SEVERITIES)}, got {sev!r}."
            )
        self.severity = _SEVERITIES[sev]

    def _match(self, position: Position) -> str | None:
        """The canonical restricted name a position hits, if any."""
        for name in position.screening_names():
            hit = self.restricted.get(_norm(name))
            if hit is not None:
                return hit
        return None

    def evaluate(self, portfolio: Portfolio) -> RuleResult:
        findings: list[Finding] = []
        hits: dict[str, list[Position]] = {}
        for p in portfolio.positions:
            matched = self._match(p)
            if matched is not None:
                hits.setdefault(matched, []).append(p)

        nav = portfolio.nav
        for name, positions in sorted(hits.items()):
            weight = sum(portfolio.base_value(p) for p in positions) / nav if nav else 0.0
            securities = ", ".join(sorted(p.security_id for p in positions))
            findings.append(
                Finding(
                    subject=name,
                    message=(
                        f"Restricted name {name!r} held via {securities} "
                        f"({pct(weight)} of the portfolio)."
                    ),
                    severity=self.severity,
                    observed=weight,
                    metric="weight",
                )
            )

        metrics = {
            "restricted_count": len(self.restricted),
            "hits": sorted(hits),
        }
        return self._new_result(findings, metrics)

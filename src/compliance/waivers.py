"""Approved exceptions (waivers) with expiry.

A waiver documents that a specific finding is a *known, approved* exception:
"Financials overweight acknowledged, approved by the CRO, expires 2026-09-30".
While the waiver is unexpired it downgrades the matching finding from BREACH (or
WARN) to :attr:`~compliance.models.Severity.ACKNOWLEDGED`, so it no longer trips
the breach gate but stays visible with its rationale. Once the expiry passes the
finding re-breaches automatically — the control cannot be silently disabled.

Waivers are applied by the engine *after* rules run, so rules stay unaware of
them; matching is by ``rule`` id plus an optional ``subject`` (the finding's
subject, e.g. an issuer or sector; omit it to waive every finding of the rule).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from compliance.models import Severity
from compliance.rules.base import Finding, RuleResult
from compliance.validation import reject_unknown_keys

_WAIVER_KEYS = frozenset({"rule", "subject", "reason", "approved_by", "expires"})


def _norm(text: str) -> str:
    return " ".join(str(text).strip().casefold().split())


@dataclass
class Waiver:
    rule: str
    reason: str
    subject: str | None = None
    approved_by: str | None = None
    expires: str | None = None  # ISO date (YYYY-MM-DD)

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "Waiver":
        reject_unknown_keys(
            f"Waiver for {config.get('rule')!r}", config, _WAIVER_KEYS
        )
        if not config.get("rule"):
            raise ValueError(f"Waiver is missing a 'rule' id: {config!r}")
        if not config.get("reason"):
            raise ValueError(
                f"Waiver for {config.get('rule')!r} needs a 'reason' (waivers must "
                f"be documented)."
            )
        expires = config.get("expires")
        if expires is not None:
            try:
                date.fromisoformat(str(expires))
            except ValueError as exc:
                raise ValueError(
                    f"Waiver for {config['rule']!r} has an invalid 'expires' date "
                    f"{expires!r}; use YYYY-MM-DD."
                ) from exc
        return cls(
            rule=str(config["rule"]),
            reason=str(config["reason"]),
            subject=(str(config["subject"]) if config.get("subject") is not None else None),
            approved_by=(str(config["approved_by"]) if config.get("approved_by") else None),
            expires=(str(expires) if expires is not None else None),
        )

    def is_active(self, as_of: date) -> bool:
        if self.expires is None:
            return True
        return date.fromisoformat(self.expires) >= as_of

    def matches(self, finding: Finding) -> bool:
        if self.subject is None:
            return True
        return _norm(self.subject) == _norm(finding.subject)

    def _detail(self) -> str:
        by = f", approved by {self.approved_by}" if self.approved_by else ""
        until = f", expires {self.expires}" if self.expires else ""
        return f"{self.reason}{by}{until}"


def as_of_date(as_of: str | None) -> date:
    """Resolve the effective date for waiver expiry (portfolio as-of, else today)."""
    if as_of:
        try:
            return date.fromisoformat(str(as_of)[:10])
        except ValueError:
            pass
    return date.today()


def apply_waivers(
    results: list[RuleResult],
    waivers: list[Waiver],
    as_of: date,
) -> None:
    """Downgrade waived findings in place and flag stale/expired waivers.

    * An active waiver that matches a WARN/BREACH finding downgrades it to
      ``ACKNOWLEDGED`` and annotates it with the rationale.
    * An expired waiver leaves the finding as-is but annotates that the waiver
      lapsed — so the reader sees a re-breach with its history.
    * An active waiver that matches nothing adds an INFO note (a possibly stale
      waiver worth reviewing).
    """
    by_rule = {r.rule_id: r for r in results}
    for waiver in waivers:
        result = by_rule.get(waiver.rule)
        if result is None:
            continue
        active = waiver.is_active(as_of)
        matched = False
        for finding in result.findings:
            if finding.severity < Severity.WARN or not waiver.matches(finding):
                continue
            matched = True
            if active:
                finding.severity = Severity.ACKNOWLEDGED
                finding.category = "WAIVER"
                finding.message = f"{finding.message} [WAIVED: {waiver._detail()}]"
            else:
                finding.message = (
                    f"{finding.message} [waiver EXPIRED {waiver.expires}: {waiver.reason}]"
                )
        if active and not matched:
            result.findings.append(
                Finding(
                    subject=waiver.subject or "(rule)",
                    message=(
                        f"Waiver did not match any current finding (possibly stale): "
                        f"{waiver._detail()}"
                    ),
                    severity=Severity.INFO,
                    category="WAIVER",
                )
            )

"""As-of / look-back comparison of a report against a prior baseline.

Given the current :class:`~compliance.report.ComplianceReport` and a prior
report (as the dict produced by :meth:`ComplianceReport.to_dict`), this computes
per-rule status transitions — what newly breached, what was resolved, what
worsened or improved — and which specific subjects (issuers, sectors, …)
appeared or cleared. This is how a monitoring desk answers "what changed since
last night's run?".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from compliance.models import Severity
from compliance.report import ComplianceReport

# Transitions, ordered most-noteworthy first for display.
TRANSITION_ORDER = [
    "NEW_BREACH",
    "WORSENED",
    "NEW_RULE",
    "RESOLVED",
    "IMPROVED",
    "REMOVED_RULE",
    "UNCHANGED",
]
_ORDER_INDEX = {t: i for i, t in enumerate(TRANSITION_ORDER)}


def _classify(prior: Severity | None, current: Severity | None) -> str:
    if prior is None:
        return "NEW_RULE"
    if current is None:
        return "REMOVED_RULE"
    if current == prior:
        return "UNCHANGED"
    if current > prior:
        return "NEW_BREACH" if current == Severity.BREACH else "WORSENED"
    return "RESOLVED" if prior == Severity.BREACH else "IMPROVED"


def _significant_subjects(findings: list[dict[str, Any]]) -> set[str]:
    """Subjects of findings at WARN or worse (the actionable ones)."""
    out: set[str] = set()
    for f in findings:
        sev = Severity[f["severity"]] if isinstance(f["severity"], str) else f["severity"]
        if sev >= Severity.WARN:
            out.add(f["subject"])
    return out


@dataclass
class RuleChange:
    rule_id: str
    transition: str
    prior_severity: str | None
    current_severity: str | None
    new_subjects: list[str]
    resolved_subjects: list[str]

    @property
    def changed(self) -> bool:
        return self.transition != "UNCHANGED"

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "transition": self.transition,
            "prior_severity": self.prior_severity,
            "current_severity": self.current_severity,
            "new_subjects": self.new_subjects,
            "resolved_subjects": self.resolved_subjects,
        }


@dataclass
class ReportComparison:
    prior_as_of: str | None
    prior_generated_at: str | None
    prior_status: str | None
    changes: list[RuleChange]

    def changed_rules(self) -> list[RuleChange]:
        return [c for c in self.changes if c.changed]

    def count(self, transition: str) -> int:
        return sum(1 for c in self.changes if c.transition == transition)

    @property
    def baseline_label(self) -> str:
        return self.prior_as_of or self.prior_generated_at or "baseline"

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline": {
                "as_of": self.prior_as_of,
                "generated_at": self.prior_generated_at,
                "status": self.prior_status,
            },
            "summary": {
                "new_breaches": self.count("NEW_BREACH"),
                "resolved": self.count("RESOLVED"),
                "worsened": self.count("WORSENED"),
                "improved": self.count("IMPROVED"),
                "new_rules": self.count("NEW_RULE"),
                "removed_rules": self.count("REMOVED_RULE"),
            },
            "changes": [c.to_dict() for c in self.changes],
        }


def compare_reports(current: ComplianceReport, prior: dict[str, Any]) -> ReportComparison:
    """Diff ``current`` against a ``prior`` report dict."""
    prior_results = {r["rule_id"]: r for r in prior.get("results", [])}
    current_results = {r.rule_id: r for r in current.results}

    changes: list[RuleChange] = []
    for rule_id in _ordered_ids(current.results, prior.get("results", [])):
        cur = current_results.get(rule_id)
        pri = prior_results.get(rule_id)

        cur_sev = cur.severity if cur else None
        pri_sev = Severity[pri["severity"]] if pri else None
        transition = _classify(pri_sev, cur_sev)

        cur_subjects = (
            {f.subject for f in cur.findings if f.severity >= Severity.WARN} if cur else set()
        )
        pri_subjects = _significant_subjects(pri["findings"]) if pri else set()

        changes.append(
            RuleChange(
                rule_id=rule_id,
                transition=transition,
                prior_severity=pri_sev.name if pri_sev is not None else None,
                current_severity=cur_sev.name if cur_sev is not None else None,
                new_subjects=sorted(cur_subjects - pri_subjects),
                resolved_subjects=sorted(pri_subjects - cur_subjects),
            )
        )

    changes.sort(key=lambda c: (_ORDER_INDEX.get(c.transition, 99), c.rule_id))
    return ReportComparison(
        prior_as_of=prior.get("as_of"),
        prior_generated_at=prior.get("generated_at"),
        prior_status=prior.get("status"),
        changes=changes,
    )


def _ordered_ids(current_results: list, prior_results: list[dict]) -> list[str]:
    """Rule ids in current order, with any prior-only ids appended."""
    ids = [r.rule_id for r in current_results]
    seen = set(ids)
    for r in prior_results:
        if r["rule_id"] not in seen:
            ids.append(r["rule_id"])
            seen.add(r["rule_id"])
    return ids

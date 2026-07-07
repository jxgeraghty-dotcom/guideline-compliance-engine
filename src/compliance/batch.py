"""Batch mode: evaluate a book of accounts against their mandates.

Monitoring runs across many accounts, not one. :func:`run_manifest` takes a
manifest of ``(portfolio, guidelines)`` pairs and produces one
:class:`AccountResult` per account, resilient to a single account failing to
load. :func:`evaluate_account` is the shared single-account pipeline used by
both the batch runner and the ``check`` command, so they stay in lock-step.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from compliance.compare import ReportComparison, compare_reports
from compliance.engine import ComplianceEngine, validate_guideline_document
from compliance.loaders import (
    LoaderError,
    load_guidelines,
    load_portfolio,
    load_report_json,
    normalize_fx_rates,
    resolve_restricted_lists,
)
from compliance.models import FxError, Severity
from compliance.report import ComplianceReport
from compliance.validation import reject_unknown_keys

#: Recognised keys of a manifest account entry.
_ACCOUNT_KEYS = frozenset(
    {"name", "portfolio", "guidelines", "baseline", "base_currency", "as_of", "metadata"}
)


def evaluate_account(
    portfolio_path: str | Path,
    guidelines_path: str | Path,
    *,
    baseline_path: str | Path | None = None,
    name: str | None = None,
    base_currency: str | None = None,
    as_of: str | None = None,
) -> tuple[ComplianceReport, ReportComparison | None]:
    """Run one portfolio against one guideline set (the shared pipeline).

    Raises :class:`~compliance.loaders.LoaderError` / ``ValueError`` on bad
    input, including an FX-coverage failure, so callers can report it uniformly.
    """
    portfolio = load_portfolio(
        portfolio_path, name=name, as_of=as_of, base_currency=base_currency
    )
    guidelines = load_guidelines(guidelines_path)
    validate_guideline_document(guidelines)
    resolve_restricted_lists(guidelines, Path(guidelines_path).resolve().parent)
    if guidelines.get("fx_rates"):
        portfolio.fx_rates.update(normalize_fx_rates(guidelines["fx_rates"]))

    missing = portfolio.missing_currencies()
    if missing:
        raise LoaderError(
            f"no FX rate for {', '.join(missing)} -> {portfolio.base_currency}. "
            f"Add an 'fx_rates' mapping to the guidelines or portfolio file."
        )

    engine = ComplianceEngine.from_config(guidelines)
    report = engine.run(portfolio)
    comparison = (
        compare_reports(report, load_report_json(baseline_path)) if baseline_path else None
    )
    return report, comparison


@dataclass
class AccountResult:
    name: str
    report: ComplianceReport | None
    comparison: ReportComparison | None = None
    error: str | None = None

    @property
    def severity(self) -> Severity:
        # An account that could not be evaluated cannot be certified.
        if self.report is None:
            return Severity.BREACH
        return self.report.overall_severity

    def to_dict(self) -> dict[str, Any]:
        if self.report is None:
            return {"name": self.name, "error": self.error, "status": "ERROR"}
        data = {
            "name": self.name,
            "status": self.report.status_label,
            "overall_severity": self.report.overall_severity.name,
            "breaches": self.report.breach_count(),
            "warnings": self.report.warn_count(),
            "acknowledged": self.report.acknowledged_count(),
        }
        if self.comparison is not None:
            data["comparison"] = self.comparison.to_dict()["summary"]
        return data


@dataclass
class BatchResult:
    results: list[AccountResult]
    generated_at: str = ""

    def worst_severity(self) -> Severity:
        return max((r.severity for r in self.results), default=Severity.PASS)

    def non_compliant_count(self) -> int:
        return sum(1 for r in self.results if r.severity >= Severity.BREACH)

    def error_count(self) -> int:
        return sum(1 for r in self.results if r.error is not None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "summary": {
                "accounts": len(self.results),
                "non_compliant": self.non_compliant_count(),
                "errors": self.error_count(),
            },
            "accounts": [r.to_dict() for r in self.results],
        }


def run_manifest(manifest: dict[str, Any], base_dir: Path) -> BatchResult:
    """Evaluate every account in a manifest, capturing per-account failures.

    A malformed account entry (unknown key, missing ``portfolio``/``guidelines``)
    is captured as that account's error rather than aborting the whole batch.
    """
    results: list[AccountResult] = []
    for account in manifest["accounts"]:
        name = _account_name(account)
        try:
            _validate_account(account)
            report, comparison = evaluate_account(
                _resolve(base_dir, account["portfolio"]),
                _resolve(base_dir, account["guidelines"]),
                baseline_path=_resolve_optional(base_dir, account.get("baseline")),
                name=account.get("name"),
                base_currency=account.get("base_currency"),
                as_of=account.get("as_of"),
            )
            results.append(AccountResult(name, report, comparison))
        except (LoaderError, ValueError, FxError, KeyError) as exc:
            results.append(AccountResult(name, None, error=str(exc)))
    return BatchResult(
        results, generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds")
    )


def _account_name(account: Any) -> str:
    if isinstance(account, dict):
        return str(account.get("name") or account.get("portfolio") or "account")
    return "account"


def _validate_account(account: Any) -> None:
    if not isinstance(account, dict):
        raise ValueError("manifest account entry must be a mapping.")
    reject_unknown_keys("Manifest account", account, _ACCOUNT_KEYS)
    for required in ("portfolio", "guidelines"):
        if not account.get(required):
            raise ValueError(f"manifest account is missing required {required!r}.")


def _resolve(base_dir: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else base_dir / path


def _resolve_optional(base_dir: Path, value: str | None) -> Path | None:
    return _resolve(base_dir, value) if value else None

"""Golden-file snapshot tests for the report renderers.

These lock the exact text and HTML output for a fixed, deterministic report so
that accidental formatting regressions are caught. Regenerate after an
intentional format change with:

    UPDATE_GOLDEN=1 python -m pytest tests/test_golden.py
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from compliance.batch import evaluate_account
from compliance.report import render_html, render_text

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
GOLDEN = Path(__file__).resolve().parent / "golden"

# A fixed timestamp so the snapshot does not depend on wall-clock time. The
# credit example pins as-of in its data, so waiver expiry is deterministic too.
FIXED_GENERATED_AT = "2026-07-07T00:00:00+00:00"


def _fixed_report():
    report, _ = evaluate_account(
        EXAMPLES / "portfolio_credit.json", EXAMPLES / "guidelines_credit.yaml"
    )
    report.generated_at = FIXED_GENERATED_AT
    return report


def _check(name: str, actual: str) -> None:
    path = GOLDEN / name
    if os.environ.get("UPDATE_GOLDEN"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(actual, encoding="utf-8")
        pytest.skip(f"updated golden {name}")
    expected = path.read_text(encoding="utf-8")
    assert actual == expected, (
        f"{name} drifted from its golden file; if intentional, regenerate with "
        f"UPDATE_GOLDEN=1."
    )


def test_golden_text_report():
    _check("credit_report.txt", render_text(_fixed_report(), color=False))


def test_golden_html_report():
    _check("credit_report.html", render_html(_fixed_report()))

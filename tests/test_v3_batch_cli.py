"""Batch runner and CLI tests for v0.3."""

from __future__ import annotations

import json
from pathlib import Path

from compliance.batch import evaluate_account, run_manifest
from compliance.cli import main
from compliance.loaders import load_manifest
from compliance.models import Severity
from compliance.report import render_batch_json, render_batch_text

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


# --------------------------------------------------------------------------- #
# Batch runner
# --------------------------------------------------------------------------- #

def test_evaluate_account_resolves_restricted_file_and_waivers():
    report, comparison = evaluate_account(
        EXAMPLES / "portfolio_credit.json", EXAMPLES / "guidelines_credit.yaml"
    )
    assert comparison is None
    by_id = {r.rule_id: r for r in report.results}
    # active waiver -> acknowledged; expired waiver on the sanctions hit -> breach
    assert by_id["CREDIT-FLOOR-01"].severity == Severity.ACKNOWLEDGED
    assert by_id["RESTRICTED-01"].severity == Severity.BREACH


def test_run_manifest_over_examples():
    manifest = load_manifest(EXAMPLES / "accounts.yaml")
    batch = run_manifest(manifest, EXAMPLES)
    assert len(batch.results) == 3
    assert batch.error_count() == 0
    assert batch.worst_severity() == Severity.BREACH
    names = {r.name for r in batch.results}
    assert "Corporate Credit - Acct 30015" in names


def test_run_manifest_is_resilient_to_a_bad_account():
    manifest = {
        "accounts": [
            {"name": "good", "portfolio": "portfolio.csv", "guidelines": "guidelines.json"},
            {"name": "bad", "portfolio": "does-not-exist.csv", "guidelines": "guidelines.json"},
        ]
    }
    batch = run_manifest(manifest, EXAMPLES)
    results = {r.name: r for r in batch.results}
    assert results["good"].report is not None
    assert results["bad"].error is not None and results["bad"].report is None
    assert results["bad"].severity == Severity.BREACH  # cannot certify -> counts against gate


def test_batch_renderers():
    batch = run_manifest(load_manifest(EXAMPLES / "accounts.yaml"), EXAMPLES)
    text = render_batch_text(batch, color=False)
    assert "BATCH COMPLIANCE SUMMARY" in text and "Acct 30015" in text
    payload = json.loads(render_batch_json(batch))
    assert payload["summary"]["accounts"] == 3
    assert len(payload["accounts"]) == 3


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def test_cli_credit_report_shows_waivers(capsys):
    code = main(
        [
            "check",
            "-p", str(EXAMPLES / "portfolio_credit.json"),
            "-g", str(EXAMPLES / "guidelines_credit.yaml"),
            "--no-color",
        ]
    )
    out = capsys.readouterr().out
    assert code == 1
    assert "WAIVED" in out                     # active waiver -> acknowledged
    assert "waiver EXPIRED" in out             # lapsed waiver re-breaches
    assert "RESTRICTED-01" in out


def test_cli_check_batch(capsys):
    code = main(["check-batch", "-m", str(EXAMPLES / "accounts.yaml"), "--no-color"])
    out = capsys.readouterr().out
    assert code == 1
    assert "BATCH COMPLIANCE SUMMARY" in out
    assert "ACTION REQUIRED" in out


def test_cli_check_batch_json(capsys):
    code = main(["check-batch", "-m", str(EXAMPLES / "accounts.yaml"), "-f", "json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload["summary"]["accounts"] == 3
    assert payload["summary"]["non_compliant"] == 3


def test_cli_check_batch_bad_manifest_exits_2(tmp_path, capsys):
    bad = tmp_path / "manifest.json"
    bad.write_text('{"not": "a manifest"}', encoding="utf-8")
    code = main(["check-batch", "-m", str(bad)])
    assert code == 2
    assert "accounts" in capsys.readouterr().err

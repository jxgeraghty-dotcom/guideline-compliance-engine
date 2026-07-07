"""Loader and CLI tests for the v0.2 features (FX, derivatives, comparison)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from compliance.cli import main
from compliance.loaders import load_portfolio, normalize_fx_rates

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


# --------------------------------------------------------------------------- #
# Loaders
# --------------------------------------------------------------------------- #

def test_load_multi_portfolio_with_derivatives_and_fx():
    portfolio = load_portfolio(
        EXAMPLES / "portfolio_multi.csv", fx_rates={"EUR": 1.08, "GBP": 1.27}
    )
    assert portfolio.position_count == 20
    assert portfolio.missing_currencies() == []

    cds = next(p for p in portfolio.positions if p.security_id == "CDS0RITEAID")
    assert cds.is_derivative
    assert cds.notional == 5_500_000
    assert cds.underlying_issuer == "Rite Aid"

    jpm = next(p for p in portfolio.positions if p.issuer == "JPMorgan Chase Bank NA")
    assert jpm.parent == "JPMorgan Chase & Co"


def test_normalize_fx_rates_casing_and_coercion():
    assert normalize_fx_rates({"eur": "1.08", "Gbp": 1.27}) == {"EUR": 1.08, "GBP": 1.27}


def test_json_portfolio_reads_fx_rates(tmp_path):
    data = {
        "name": "FX Book",
        "base_currency": "USD",
        "fx_rates": {"EUR": 1.09},
        "positions": [
            {"security_id": "E", "issuer": "Euro Co", "market_value": 100, "currency": "EUR"}
        ],
    }
    path = tmp_path / "p.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    portfolio = load_portfolio(path)
    assert portfolio.fx_rates == {"EUR": 1.09}
    assert portfolio.base_value(portfolio.positions[0]) == pytest.approx(109)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def test_cli_multi_run_reports_currency_rule(capsys):
    code = main(
        [
            "check",
            "-p", str(EXAMPLES / "portfolio_multi.csv"),
            "-g", str(EXAMPLES / "guidelines_multi.yaml"),
            "--no-color",
        ]
    )
    out = capsys.readouterr().out
    assert code == 1
    assert "NON-COMPLIANT" in out
    assert "CURRENCY-EXP-01" in out
    assert "rolls up" in out  # ultimate-parent aggregation surfaced


def test_cli_missing_fx_rate_exits_2(capsys):
    # The multi portfolio holds EUR/GBP but the basic guidelines define no rates.
    code = main(
        [
            "check",
            "-p", str(EXAMPLES / "portfolio_multi.csv"),
            "-g", str(EXAMPLES / "guidelines.json"),
        ]
    )
    err = capsys.readouterr().err
    assert code == 2
    assert "FX rate" in err


def test_cli_baseline_comparison(tmp_path, capsys):
    baseline = tmp_path / "baseline.json"
    # Period 1: the prior (compliant) snapshot -> baseline.
    code1 = main(
        [
            "check",
            "-p", str(EXAMPLES / "portfolio_prior.csv"),
            "-g", str(EXAMPLES / "guidelines.json"),
            "-f", "json",
            "-o", str(baseline),
        ]
    )
    capsys.readouterr()
    assert code1 == 0 and baseline.exists()

    # Period 2: the current book, compared to the baseline.
    code2 = main(
        [
            "check",
            "-p", str(EXAMPLES / "portfolio.csv"),
            "-g", str(EXAMPLES / "guidelines.json"),
            "--baseline", str(baseline),
            "--no-color",
        ]
    )
    out = capsys.readouterr().out
    assert code2 == 1
    assert "CHANGES SINCE BASELINE" in out
    assert "NEW_BREACH" in out


def test_cli_bad_baseline_exits_2(tmp_path, capsys):
    junk = tmp_path / "junk.json"
    junk.write_text('{"not": "a report"}', encoding="utf-8")
    code = main(
        [
            "check",
            "-p", str(EXAMPLES / "portfolio.csv"),
            "-g", str(EXAMPLES / "guidelines.json"),
            "--baseline", str(junk),
        ]
    )
    assert code == 2
    assert "does not look like a report" in capsys.readouterr().err


def test_cli_malformed_baseline_results_exits_2(tmp_path, capsys):
    # Structurally a report, but a results entry lacks the fields the diff
    # relies on: must be a clean input error (exit 2), not a KeyError crash.
    bad = tmp_path / "baseline.json"
    bad.write_text(json.dumps({"results": [{"rule_id": "R"}]}), encoding="utf-8")
    code = main(
        [
            "check",
            "-p", str(EXAMPLES / "portfolio.csv"),
            "-g", str(EXAMPLES / "guidelines.json"),
            "--baseline", str(bad),
        ]
    )
    assert code == 2
    assert "severity" in capsys.readouterr().err


def test_cli_list_rules_includes_currency(capsys):
    main(["list-rules"])
    assert "currency_exposure" in capsys.readouterr().out

"""Tests for loaders and the CLI end-to-end behaviour."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from compliance.cli import main
from compliance.loaders import LoaderError, load_guidelines, load_portfolio

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


# --------------------------------------------------------------------------- #
# Loaders
# --------------------------------------------------------------------------- #

def test_load_portfolio_csv():
    portfolio = load_portfolio(EXAMPLES / "portfolio.csv")
    assert portfolio.position_count == 21
    assert portfolio.total_market_value == pytest.approx(100_000_000)
    treasury = [p for p in portfolio.positions if p.issuer == "US Treasury"]
    assert len(treasury) == 4


def test_load_portfolio_csv_missing_column(tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("issuer,market_value\nAcme,100\n", encoding="utf-8")
    with pytest.raises(LoaderError):
        load_portfolio(bad)


def test_load_portfolio_json(tmp_path):
    data = {
        "name": "JSON Port",
        "base_currency": "EUR",
        "positions": [
            {"security_id": "S1", "issuer": "Acme", "market_value": 100, "rating": "A"}
        ],
    }
    path = tmp_path / "p.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    portfolio = load_portfolio(path)
    assert portfolio.name == "JSON Port"
    assert portfolio.base_currency == "EUR"
    assert portfolio.positions[0].rating == "A"


def test_load_guidelines_json_and_yaml_agree():
    from_json = load_guidelines(EXAMPLES / "guidelines.json")
    from_yaml = load_guidelines(EXAMPLES / "guidelines.yaml")
    ids_json = [g["id"] for g in from_json["guidelines"]]
    ids_yaml = [g["id"] for g in from_yaml["guidelines"]]
    assert ids_json == ids_yaml


def test_load_portfolio_missing_file():
    with pytest.raises(LoaderError):
        load_portfolio("does-not-exist.csv")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def test_cli_check_exits_1_on_breach(capsys):
    code = main(
        [
            "check",
            "-p", str(EXAMPLES / "portfolio.csv"),
            "-g", str(EXAMPLES / "guidelines.json"),
            "--no-color",
        ]
    )
    out = capsys.readouterr().out
    assert code == 1               # breaches present -> non-zero (gate fails)
    assert "NON-COMPLIANT" in out
    assert "ISSUER-CONC-01" in out


def test_cli_check_json_output(capsys):
    code = main(
        [
            "check",
            "-p", str(EXAMPLES / "portfolio.csv"),
            "-g", str(EXAMPLES / "guidelines.json"),
            "-f", "json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload["status"] == "NON-COMPLIANT"


def test_cli_fail_on_never_exits_0(capsys):
    code = main(
        [
            "check",
            "-p", str(EXAMPLES / "portfolio.csv"),
            "-g", str(EXAMPLES / "guidelines.json"),
            "--fail-on", "never",
            "--no-color",
        ]
    )
    capsys.readouterr()
    assert code == 0


def test_cli_writes_html_output(tmp_path, capsys):
    out_path = tmp_path / "report.html"
    code = main(
        [
            "check",
            "-p", str(EXAMPLES / "portfolio.csv"),
            "-g", str(EXAMPLES / "guidelines.json"),
            "-f", "html",
            "-o", str(out_path),
        ]
    )
    assert code == 1
    assert out_path.exists()
    assert out_path.read_text(encoding="utf-8").startswith("<!DOCTYPE html>")


def test_cli_bad_portfolio_exits_2(capsys):
    code = main(["check", "-p", "nope.csv", "-g", str(EXAMPLES / "guidelines.json")])
    assert code == 2
    assert "error:" in capsys.readouterr().err


def test_cli_unexpected_crash_exits_2(monkeypatch, capsys):
    # Exit code 1 means "breach found"; an internal bug must exit 2 so a CI
    # gate can tell a tooling failure from a compliance failure.
    import compliance.cli as cli

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(cli, "evaluate_account", boom)
    code = cli.main(
        ["check", "-p", str(EXAMPLES / "portfolio.csv"),
         "-g", str(EXAMPLES / "guidelines.json")]
    )
    assert code == 2
    assert "boom" in capsys.readouterr().err


def test_cli_list_rules(capsys):
    code = main(["list-rules"])
    out = capsys.readouterr().out
    assert code == 0
    for rule_type in ("issuer_concentration", "credit_floor", "duration_band", "sector_cap"):
        assert rule_type in out

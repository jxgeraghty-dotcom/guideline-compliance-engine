"""Strict config-key validation: unknown keys must fail loudly, not be ignored."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from compliance.batch import run_manifest
from compliance.engine import ComplianceEngine
from compliance.loaders import LoaderError, load_manifest, load_portfolio
from compliance.rules.base import create_rule
from compliance.rules.issuer_concentration import IssuerConcentrationRule
from compliance.validation import reject_unknown_keys
from compliance.waivers import Waiver

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def test_reject_unknown_keys_suggests_near_miss():
    with pytest.raises(ValueError) as exc:
        reject_unknown_keys("ctx", ["look_throuh"], {"look_through", "max_weight"})
    assert "did you mean 'look_through'" in str(exc.value)


def test_valid_rule_config_is_accepted():
    rule = IssuerConcentrationRule({"id": "R", "max_weight": 0.05, "look_through": True})
    assert rule.rule_id == "R"


def test_unknown_rule_key_is_rejected():
    with pytest.raises(ValueError, match="unknown config key"):
        IssuerConcentrationRule({"max_weight": 0.05, "exempt_sectrs": ["Government"]})


def test_typo_that_would_disable_a_control_is_rejected():
    # 'look_throuh' would silently leave look-through OFF and miss a breach.
    with pytest.raises(ValueError) as exc:
        IssuerConcentrationRule({"max_weight": 0.05, "look_throuh": True})
    assert "look_through" in str(exc.value)


def test_create_rule_validates_keys():
    with pytest.raises(ValueError, match="unknown config key"):
        create_rule({"type": "sector_cap", "max_weight": 0.25, "warn_rato": 0.9})


def test_engine_from_config_propagates_rule_validation():
    config = {"guidelines": [{"id": "S", "type": "sector_cap", "max_weight": 0.25, "bogus": 1}]}
    with pytest.raises(ValueError, match="unknown config key"):
        ComplianceEngine.from_config(config)


def test_common_keys_always_allowed():
    # id/type/description are accepted on every rule.
    create_rule({"id": "X", "type": "sector_cap", "description": "d", "max_weight": 0.25})


def test_waiver_unknown_key_rejected():
    # 'expiry' instead of 'expires' would make the waiver never lapse.
    with pytest.raises(ValueError) as exc:
        Waiver.from_config({"rule": "R", "reason": "x", "expiry": "2026-01-01"})
    assert "expires" in str(exc.value)


def test_waiver_valid_config_accepted():
    w = Waiver.from_config(
        {"rule": "R", "subject": "S", "reason": "x", "approved_by": "A", "expires": "2026-12-31"}
    )
    assert w.rule == "R" and w.expires == "2026-12-31"


# --------------------------------------------------------------------------- #
# Top-level guideline document
# --------------------------------------------------------------------------- #

_SECTOR_DOC = {"guidelines": [{"id": "S", "type": "sector_cap", "max_weight": 0.25}]}


def test_document_misspelled_guidelines_key_is_rejected():
    with pytest.raises(ValueError) as exc:
        ComplianceEngine.from_config({"guidlines": _SECTOR_DOC["guidelines"]})
    assert "guidelines" in str(exc.value)


def test_document_misspelled_fx_rates_key_is_rejected():
    with pytest.raises(ValueError) as exc:
        ComplianceEngine.from_config(dict(_SECTOR_DOC, fx_rate={"EUR": 1.1}))
    assert "fx_rates" in str(exc.value)


def test_document_allows_known_metadata_and_free_form_block():
    engine = ComplianceEngine.from_config(
        dict(
            _SECTOR_DOC,
            portfolio_name="Acct 1",
            base_currency="USD",
            fx_rates={"EUR": 1.1},
            metadata={"owner": "desk A", "notes": "anything goes here"},
        )
    )
    assert len(engine.rules) == 1


# --------------------------------------------------------------------------- #
# Batch manifest
# --------------------------------------------------------------------------- #

def test_manifest_unknown_top_level_key_is_rejected(tmp_path):
    bad = tmp_path / "m.json"
    bad.write_text('{"accounts": [], "acounts": []}', encoding="utf-8")
    with pytest.raises(LoaderError) as exc:
        load_manifest(bad)
    assert "did you mean 'accounts'" in str(exc.value)


def test_manifest_account_unknown_key_becomes_account_error():
    manifest = {
        "accounts": [
            {"name": "typo", "portfolio": "portfolio.csv",
             "guidlines": "guidelines.json"},  # misspelled 'guidelines'
        ]
    }
    batch = run_manifest(manifest, EXAMPLES)
    result = batch.results[0]
    assert result.report is None
    assert "guidelines" in (result.error or "")   # suggestion + missing-required


def test_manifest_account_missing_required_becomes_account_error():
    manifest = {"accounts": [{"name": "x", "portfolio": "portfolio.csv"}]}
    batch = run_manifest(manifest, EXAMPLES)
    assert "guidelines" in (batch.results[0].error or "")


def test_valid_manifest_still_loads():
    manifest = load_manifest(EXAMPLES / "accounts.yaml")
    assert len(manifest["accounts"]) == 3


# --------------------------------------------------------------------------- #
# JSON portfolio documents
# --------------------------------------------------------------------------- #

def _write_portfolio(tmp_path, payload) -> Path:
    path = tmp_path / "p.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_json_position_unknown_key_is_rejected(tmp_path):
    # 'underlying_isuer' would silently drop the look-through attribution.
    path = _write_portfolio(tmp_path, {
        "positions": [
            {"security_id": "A", "issuer": "A Co", "market_value": 100,
             "underlying_isuer": "RiskCo"},
        ]
    })
    with pytest.raises(LoaderError) as exc:
        load_portfolio(path)
    assert "underlying_issuer" in str(exc.value)


def test_json_portfolio_unknown_top_level_key_is_rejected(tmp_path):
    path = _write_portfolio(tmp_path, {"fx_rate": {"EUR": 1.1}, "positions": []})
    with pytest.raises(LoaderError) as exc:
        load_portfolio(path)
    assert "fx_rates" in str(exc.value)


def test_json_position_metadata_block_is_allowed(tmp_path):
    path = _write_portfolio(tmp_path, {
        "positions": [
            {"security_id": "A", "issuer": "A Co", "market_value": 100,
             "metadata": {"lot": 42}},
        ],
        "metadata": {"source": "custodian feed"},
    })
    portfolio = load_portfolio(path)
    assert portfolio.position_count == 1

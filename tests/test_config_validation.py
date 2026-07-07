"""Strict config-key validation: unknown keys must fail loudly, not be ignored."""

from __future__ import annotations

import pytest

from compliance.engine import ComplianceEngine
from compliance.rules.base import create_rule
from compliance.rules.issuer_concentration import IssuerConcentrationRule
from compliance.validation import reject_unknown_keys
from compliance.waivers import Waiver


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

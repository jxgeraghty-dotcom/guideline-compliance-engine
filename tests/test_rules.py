"""Tests for the four built-in guideline rules."""

from __future__ import annotations

import pytest

from compliance.models import Portfolio, Severity
from compliance.rules.base import create_rule
from compliance.rules.credit_floor import CreditFloorRule
from compliance.rules.duration_band import DurationBandRule
from compliance.rules.issuer_concentration import IssuerConcentrationRule
from compliance.rules.sector_cap import SectorCapRule
from conftest import pos


# --------------------------------------------------------------------------- #
# Issuer concentration
# --------------------------------------------------------------------------- #

def test_issuer_concentration_breach_warn_and_exemption(simple_portfolio):
    rule = IssuerConcentrationRule(
        {"max_weight": 0.05, "warn_at": 0.045, "exempt_sectors": ["Government"]}
    )
    result = rule.evaluate(simple_portfolio)
    assert result.severity == Severity.BREACH

    breaches = {f.subject for f in result.breaches()}
    warns = {f.subject for f in result.warnings()}
    assert breaches == {"Alpha Corp", "Epsilon"}   # 8% and 10%
    assert "Delta SA" in warns                      # exactly at 5% -> warn
    # Government issuers are exempt regardless of size.
    subjects = {f.subject for f in result.findings}
    assert "Zeta" not in subjects and "US Treasury" not in subjects


def test_issuer_concentration_override():
    # Single issuer across two lines -> 100% concentration in "Big Name".
    portfolio = Portfolio(
        name="p",
        positions=[pos("X", "Big Name", 8), pos("Y", "Big Name", 2)],
    )
    # A 10% override still cannot accommodate a 100% position -> breach.
    rule = IssuerConcentrationRule({"max_weight": 0.05, "overrides": {"Big Name": 0.10}})
    assert rule.evaluate(portfolio).severity == Severity.BREACH
    # Lifting the override to 100% clears the breach, leaving a warn at the cap.
    rule2 = IssuerConcentrationRule({"max_weight": 0.05, "overrides": {"Big Name": 1.0}})
    assert rule2.evaluate(portfolio).severity == Severity.WARN


def test_issuer_concentration_requires_max_weight():
    with pytest.raises(ValueError):
        IssuerConcentrationRule({})


# --------------------------------------------------------------------------- #
# Credit floor
# --------------------------------------------------------------------------- #

def test_credit_floor_hard_floor_breach(simple_portfolio):
    rule = CreditFloorRule({"min_rating": "BBB-", "max_below_weight": 0.0})
    result = rule.evaluate(simple_portfolio)
    assert result.severity == Severity.BREACH
    breach = result.breaches()[0]
    assert "Gamma" in breach.message  # BB+ holding below the floor


def test_credit_floor_bucket_allows_small_weight(simple_portfolio):
    rule = CreditFloorRule({"min_rating": "BBB-", "max_below_weight": 0.05})
    result = rule.evaluate(simple_portfolio)
    # 3% below floor is within the 5% allowance -> compliant.
    assert result.severity == Severity.PASS
    assert result.metrics["below_floor_weight"] == pytest.approx(0.03)


def test_credit_floor_unrated_flag():
    portfolio = Portfolio(
        name="p",
        positions=[pos("R", "Rated", 90, rating="A"), pos("U", "Unrated", 10, rating="NR")],
    )
    rule = CreditFloorRule({"min_rating": "BBB-", "treat_unrated_as": "warn"})
    result = rule.evaluate(portfolio)
    assert result.severity == Severity.WARN
    data_flags = [f for f in result.findings if f.category == "DATA"]
    assert len(data_flags) == 1 and "Unrated" in data_flags[0].message


def test_credit_floor_rejects_bad_min_rating():
    with pytest.raises(ValueError):
        CreditFloorRule({"min_rating": "ZZZ"})


# --------------------------------------------------------------------------- #
# Duration band
# --------------------------------------------------------------------------- #

def test_duration_within_band(simple_portfolio):
    rule = DurationBandRule({"min_duration": 3.0, "max_duration": 7.0})
    result = rule.evaluate(simple_portfolio)
    assert result.severity == Severity.PASS
    assert result.metrics["portfolio_duration"] == pytest.approx(5.71)


def test_duration_below_band_breach(simple_portfolio):
    rule = DurationBandRule({"min_duration": 6.0, "max_duration": 8.0})
    result = rule.evaluate(simple_portfolio)
    assert result.severity == Severity.BREACH
    assert "below" in result.breaches()[0].message


def test_duration_warn_buffer(simple_portfolio):
    # Duration 5.71; upper edge 6.0 with a 0.5 buffer -> within the warn zone.
    rule = DurationBandRule({"min_duration": 3.0, "max_duration": 6.0, "warn_buffer": 0.5})
    result = rule.evaluate(simple_portfolio)
    assert result.severity == Severity.WARN


def test_duration_missing_data_flag():
    portfolio = Portfolio(
        name="p",
        positions=[
            pos("A", "A", 50, rating="A", duration=5.0),
            pos("B", "B", 50, rating="A", duration=None),  # fixed income, no duration
        ],
    )
    rule = DurationBandRule({"min_duration": 0.0, "max_duration": 10.0})
    result = rule.evaluate(portfolio)
    assert any(f.category == "DATA" for f in result.findings)


def test_duration_rejects_inverted_band():
    with pytest.raises(ValueError):
        DurationBandRule({"min_duration": 7.0, "max_duration": 3.0})


# --------------------------------------------------------------------------- #
# Sector cap
# --------------------------------------------------------------------------- #

def test_sector_cap_breach(simple_portfolio):
    rule = SectorCapRule({"max_weight": 0.25})
    result = rule.evaluate(simple_portfolio)
    assert result.severity == Severity.BREACH
    assert result.breaches()[0].subject == "Government"  # 70%


def test_sector_cap_override_and_warn(simple_portfolio):
    rule = SectorCapRule({"max_weight": 0.25, "overrides": {"Government": 0.75}})
    result = rule.evaluate(simple_portfolio)
    # 70% within the 75% override but >= 90% of it -> warn.
    assert result.severity == Severity.WARN


def test_sector_cap_floor_shortfall(simple_portfolio):
    rule = SectorCapRule(
        {"max_weight": 0.75, "floors": {"Technology": 0.15}}
    )
    result = rule.evaluate(simple_portfolio)
    # Technology is 10%, below the 15% floor -> breach.
    assert result.severity == Severity.BREACH
    assert any("below the" in f.message for f in result.breaches())


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #

def test_create_rule_via_registry():
    rule = create_rule({"type": "sector_cap", "max_weight": 0.25})
    assert isinstance(rule, SectorCapRule)


def test_create_rule_unknown_type():
    with pytest.raises(ValueError):
        create_rule({"type": "does_not_exist"})

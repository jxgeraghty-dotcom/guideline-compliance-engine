"""Tests for the v0.2 features: FX, ultimate-parent, look-through, currency,
and baseline comparison."""

from __future__ import annotations

import pytest

from compliance.compare import compare_reports
from compliance.engine import ComplianceEngine
from compliance.models import FxError, Portfolio, Position, Severity
from compliance.rules.credit_floor import CreditFloorRule
from compliance.rules.currency_exposure import CurrencyExposureRule
from compliance.rules.duration_band import DurationBandRule
from compliance.rules.issuer_concentration import IssuerConcentrationRule
from compliance.rules.sector_cap import SectorCapRule
from conftest import pos


# --------------------------------------------------------------------------- #
# Multi-currency model
# --------------------------------------------------------------------------- #

def test_fx_conversion_and_weights():
    p = Portfolio(
        name="p",
        base_currency="USD",
        fx_rates={"EUR": 1.10},
        positions=[
            pos("E", "Euro Co", 1_000_000, currency="EUR"),
            pos("U", "Dollar Co", 1_000_000, currency="USD"),
        ],
    )
    assert p.base_value(p.positions[0]) == pytest.approx(1_100_000)
    assert p.nav == pytest.approx(2_100_000)
    assert p.weight(p.positions[0]) == pytest.approx(1_100_000 / 2_100_000)
    assert p.missing_currencies() == []


def test_missing_fx_rate_is_detected_and_raises():
    p = Portfolio(
        name="p",
        base_currency="USD",
        positions=[pos("G", "Gilt Co", 1_000_000, currency="GBP")],
    )
    assert p.missing_currencies() == ["GBP"]
    assert p.rate("GBP") is None
    with pytest.raises(FxError):
        p.nav  # noqa: B018 - property access triggers conversion


def test_derivative_negative_mark_allowed_but_not_cash():
    Position("S", "Swap Co", -50_000, instrument_type="swap")  # ok: derivative
    with pytest.raises(ValueError):
        Position("B", "Bond Co", -50_000)  # cash instrument, long-only


def test_base_exposure_uses_notional_for_derivatives():
    cds = Position("C", "CDS", 100_000, instrument_type="cds", notional=5_000_000)
    p = Portfolio(name="p", positions=[cds])
    assert cds.local_exposure() == 5_000_000
    assert p.base_exposure(cds) == 5_000_000
    assert p.base_value(cds) == 100_000


# --------------------------------------------------------------------------- #
# Ultimate-parent aggregation
# --------------------------------------------------------------------------- #

def _parent_book() -> Portfolio:
    return Portfolio(
        name="p",
        positions=[
            pos("A1", "Entity A", 3, sector="Financials", ultimate_parent="Group"),
            pos("A2", "Entity B", 3, sector="Financials", ultimate_parent="Group"),
            pos("G", "US Treasury", 94, sector="Government"),
        ],
    )


def test_parent_aggregation_breaches_where_issuer_level_passes():
    book = _parent_book()
    at_issuer = IssuerConcentrationRule(
        {"max_weight": 0.05, "exempt_sectors": ["Government"]}
    )
    assert at_issuer.evaluate(book).severity == Severity.PASS  # 3% + 3% separately

    at_parent = IssuerConcentrationRule(
        {"max_weight": 0.05, "level": "ultimate_parent", "exempt_sectors": ["Government"]}
    )
    result = at_parent.evaluate(book)
    assert result.severity == Severity.BREACH        # 6% aggregated to the group
    breach = result.breaches()[0]
    assert breach.subject == "Group" and "rolls up" in breach.message


# --------------------------------------------------------------------------- #
# Derivatives look-through
# --------------------------------------------------------------------------- #

def _cds_book(rating="CCC", sector="Credit") -> Portfolio:
    return Portfolio(
        name="p",
        positions=[
            pos("G", "US Treasury", 90, sector="Government", rating="AA+", duration=5.0),
            pos(
                "CDS", "CDS Desk", 1, sector=sector, rating=rating, duration=None,
                instrument_type="cds", notional=8, underlying_issuer="RiskCo",
                underlying_sector="Energy",
            ),
        ],
    )


def test_issuer_look_through_attributes_notional_to_reference():
    book = _cds_book()
    off = IssuerConcentrationRule({"max_weight": 0.05, "exempt_sectors": ["Government"]})
    assert off.evaluate(book).severity == Severity.PASS  # CDS mark is only ~1%

    on = IssuerConcentrationRule(
        {"max_weight": 0.05, "exempt_sectors": ["Government"], "look_through": True}
    )
    result = on.evaluate(book)
    assert result.severity == Severity.BREACH            # 8 / 91 ~ 8.8% on RiskCo
    assert result.breaches()[0].subject == "RiskCo"


def test_credit_look_through_counts_notional_below_floor():
    book = _cds_book(rating="CCC")
    off = CreditFloorRule({"min_rating": "BBB-", "max_below_weight": 0.05})
    assert off.evaluate(book).severity == Severity.PASS  # ~1% mark below floor

    on = CreditFloorRule(
        {"min_rating": "BBB-", "max_below_weight": 0.05, "look_through": True}
    )
    result = on.evaluate(book)
    assert result.severity == Severity.BREACH
    assert result.metrics["below_floor_weight"] == pytest.approx(8 / 91)


def test_sector_look_through_moves_exposure_to_underlying():
    book = Portfolio(
        name="p",
        positions=[
            pos("B", "BondCo", 90, sector="Technology"),
            pos(
                "F", "FutCo", 10, sector="Financials", instrument_type="future",
                notional=40, underlying_sector="Energy",
            ),
        ],
    )
    off = SectorCapRule({"max_weight": 0.25}).evaluate(book)
    assert "Energy" not in off.metrics["sector_weights"]
    assert off.metrics["sector_weights"]["Financials"] == pytest.approx(0.10)

    on = SectorCapRule({"max_weight": 0.25, "look_through": True}).evaluate(book)
    assert on.metrics["sector_weights"]["Energy"] == pytest.approx(0.40)


def test_duration_look_through_uses_notional():
    book = Portfolio(
        name="p",
        positions=[
            pos("B", "BondCo", 100, duration=2.0),
            pos("F", "FutCo", 0, instrument_type="future", notional=100, duration=10.0),
        ],
    )
    off = DurationBandRule({"min_duration": 0.0, "max_duration": 20.0}).evaluate(book)
    assert off.metrics["portfolio_duration"] == pytest.approx(2.0)  # future mark is 0

    on = DurationBandRule(
        {"min_duration": 0.0, "max_duration": 20.0, "look_through": True}
    ).evaluate(book)
    assert on.metrics["portfolio_duration"] == pytest.approx(12.0)  # 2 + 10 overlay


# --------------------------------------------------------------------------- #
# Currency exposure
# --------------------------------------------------------------------------- #

def test_currency_exposure_per_currency_and_aggregate():
    book = Portfolio(
        name="p",
        base_currency="USD",
        fx_rates={"EUR": 1.0, "GBP": 1.0},
        positions=[
            pos("U", "Dollar Co", 80, currency="USD"),
            pos("E", "Euro Co", 10, currency="EUR"),
            pos("G", "Sterling Co", 10, currency="GBP"),
        ],
    )
    rule = CurrencyExposureRule(
        {"max_per_currency": 0.08, "max_aggregate_foreign": 0.15}
    )
    result = rule.evaluate(book)
    assert result.severity == Severity.BREACH
    subjects = {f.subject for f in result.findings}
    assert {"EUR", "GBP", "foreign currency (aggregate)"} <= subjects
    # the base currency is never itself flagged
    assert "USD" not in subjects
    assert result.metrics["foreign_weight"] == pytest.approx(0.20)


# --------------------------------------------------------------------------- #
# Baseline comparison
# --------------------------------------------------------------------------- #

_SECTOR_ONLY = {"guidelines": [{"id": "SEC", "type": "sector_cap", "max_weight": 0.25}]}


def _run(book: Portfolio):
    return ComplianceEngine.from_config(_SECTOR_ONLY).run(book)


def test_compare_detects_new_and_resolved_breach():
    compliant = Portfolio(
        name="p",
        positions=[
            pos("G", "Govt", 20, sector="Government"),
            pos("F", "Fin", 20, sector="Financials"),
            pos("T", "Tech", 20, sector="Technology"),
            pos("U", "Util", 20, sector="Utilities"),
            pos("I", "Ind", 20, sector="Industrials"),
        ],
    )  # every sector at 20% < 25% cap
    breaching = Portfolio(
        name="p",
        positions=[
            pos("G", "Govt", 70, sector="Government"),
            pos("F", "Fin", 15, sector="Financials"),
            pos("T", "Tech", 15, sector="Technology"),
        ],
    )  # Government at 70% breaches

    prior_ok = _run(compliant)
    now_bad = _run(breaching)

    worse = compare_reports(now_bad, prior_ok.to_dict())
    assert worse.count("NEW_BREACH") == 1
    change = worse.changes[0]
    assert change.transition == "NEW_BREACH" and "Government" in change.new_subjects

    better = compare_reports(prior_ok, now_bad.to_dict())
    assert better.count("RESOLVED") == 1
    assert "Government" in better.changes[0].resolved_subjects


def test_compare_unchanged_when_identical():
    book = Portfolio(name="p", positions=[pos("G", "Govt", 70, sector="Government")])
    report = _run(book)
    comparison = compare_reports(report, report.to_dict())
    assert comparison.changed_rules() == []
    assert comparison.count("UNCHANGED") == 1

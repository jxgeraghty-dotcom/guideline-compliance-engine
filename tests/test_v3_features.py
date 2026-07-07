"""Tests for the v0.3 features: tolerance, netting, rating basis, restricted
list and waivers."""

from __future__ import annotations

import pytest
from conftest import pos

from compliance import ratings
from compliance.engine import ComplianceEngine
from compliance.models import Portfolio, Position, Severity
from compliance.rules.credit_floor import CreditFloorRule
from compliance.rules.issuer_concentration import IssuerConcentrationRule
from compliance.rules.restricted_list import RestrictedListRule
from compliance.tolerance import at_least, below, exceeds
from compliance.waivers import Waiver

# --------------------------------------------------------------------------- #
# Boundary tolerance
# --------------------------------------------------------------------------- #

def test_tolerance_helpers():
    assert exceeds(0.05 + 1e-6, 0.05) is True
    assert exceeds(0.05, 0.05) is False           # exactly on the limit is not a breach
    assert below(0.05 - 1e-6, 0.05) is True
    assert below(0.05, 0.05) is False
    assert at_least(0.05, 0.05) is True


def test_position_exactly_at_limit_is_not_a_breach():
    book = Portfolio(
        name="p",
        positions=[
            pos("A", "Acme", 5, sector="Financials"),      # exactly 5%
            pos("G", "US Treasury", 95, sector="Government"),
        ],
    )
    rule = IssuerConcentrationRule({"max_weight": 0.05, "exempt_sectors": ["Government"]})
    result = rule.evaluate(book)
    assert result.severity < Severity.BREACH   # at the cap -> warn, never breach


# --------------------------------------------------------------------------- #
# Netting (gross vs net)
# --------------------------------------------------------------------------- #

def _hedged_book() -> Portfolio:
    return Portfolio(
        name="p",
        positions=[
            pos("G", "US Treasury", 90, sector="Government"),
            pos("S", "CDS Sold", 0.1, sector="Credit", instrument_type="cds",
                notional=6, underlying_issuer="RiskCo"),
            pos("B", "CDS Bought", 0.1, sector="Credit", instrument_type="cds",
                notional=-4, underlying_issuer="RiskCo"),  # protection bought (hedge)
        ],
    )


def test_netting_net_lets_hedge_offset():
    rule = IssuerConcentrationRule(
        {"max_weight": 0.05, "exempt_sectors": ["Government"],
         "look_through": True, "netting": "net"}
    )
    # +6 and -4 net to +2 (~2.2%) -> within the 5% limit.
    assert rule.evaluate(_hedged_book()).severity < Severity.BREACH


def test_netting_gross_sums_absolute_exposure():
    rule = IssuerConcentrationRule(
        {"max_weight": 0.05, "exempt_sectors": ["Government"],
         "look_through": True, "netting": "gross"}
    )
    result = rule.evaluate(_hedged_book())
    # |6| + |4| = 10 (~11%) on RiskCo -> breach.
    assert result.severity == Severity.BREACH
    assert result.breaches()[0].subject == "RiskCo"


# --------------------------------------------------------------------------- #
# Multi-agency rating basis
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "agencies,basis,expected",
    [
        (["A", "BBB"], "lower", "BBB"),
        (["A", "BBB"], "higher", "A"),
        (["A", "BBB"], "median", "BBB"),          # lower of two
        (["AAA", "A", "BBB"], "median", "A"),      # middle of three
        (["Baa3", "BBB"], "lower", "BBB-"),        # Moody's normalised in
        (["NR", ""], "lower", None),
    ],
)
def test_effective_rating(agencies, basis, expected):
    assert ratings.effective_rating(agencies, basis) == expected


def test_position_effective_rating_falls_back_to_single_field():
    p = Position("X", "X", 1, rating="BBB")
    assert p.effective_rating("lower") == "BBB"


def test_credit_floor_rating_basis_changes_classification():
    book = Portfolio(
        name="p",
        positions=[
            pos("SPLIT", "Split Co", 10, rating_sp="BBB-", rating_moody="Ba1"),
            pos("SAFE", "Safe Co", 90, rating_sp="A"),
        ],
    )
    lower = CreditFloorRule(
        {"min_rating": "BBB-", "max_below_weight": 0.05, "rating_basis": "lower"}
    ).evaluate(book)
    assert lower.severity == Severity.BREACH        # Ba1 == BB+ drops it below the floor

    higher = CreditFloorRule(
        {"min_rating": "BBB-", "max_below_weight": 0.05, "rating_basis": "higher"}
    ).evaluate(book)
    assert higher.severity == Severity.PASS         # BBB- clears the floor


# --------------------------------------------------------------------------- #
# Restricted list
# --------------------------------------------------------------------------- #

def test_restricted_list_matches_issuer_parent_and_underlying():
    book = Portfolio(
        name="p",
        positions=[
            pos("A", "Clean Co", 40),
            pos("B", "Sub Co", 20, ultimate_parent="Bad Parent"),
            pos("C", "CDS", 40, instrument_type="cds", notional=10,
                underlying_issuer="Bad Reference"),
        ],
    )
    rule = RestrictedListRule({"names": ["bad parent", "BAD REFERENCE"]})
    result = rule.evaluate(book)
    assert result.severity == Severity.BREACH
    assert {f.subject for f in result.findings} == {"bad parent", "BAD REFERENCE"}


def test_restricted_list_severity_and_empty():
    book = Portfolio(name="p", positions=[pos("A", "Acme", 100)])
    warn_rule = RestrictedListRule({"names": ["Acme"], "severity": "warn"})
    assert warn_rule.evaluate(book).severity == Severity.WARN
    with pytest.raises(ValueError):
        RestrictedListRule({"names": []})


def test_restricted_list_from_file(tmp_path):
    listing = tmp_path / "restricted.txt"
    listing.write_text("# header\nAcme Corp\n\n", encoding="utf-8")
    rule = RestrictedListRule({"file": str(listing)})
    book = Portfolio(name="p", positions=[pos("A", "Acme Corp", 100)])
    assert rule.evaluate(book).severity == Severity.BREACH


# --------------------------------------------------------------------------- #
# Waivers
# --------------------------------------------------------------------------- #

_WAIVED_GUIDELINES = {
    "guidelines": [{"id": "SEC", "type": "sector_cap", "max_weight": 0.25}],
}


def _breaching_book() -> Portfolio:
    return Portfolio(
        name="p",
        as_of="2026-07-07",
        positions=[pos("G", "Govt", 70, sector="Government")],
    )


def _run_with_waivers(waivers: list[dict]):
    config = dict(_WAIVED_GUIDELINES, waivers=waivers)
    return ComplianceEngine.from_config(config).run(_breaching_book())


def test_active_waiver_downgrades_to_acknowledged():
    report = _run_with_waivers(
        [{"rule": "SEC", "subject": "Government", "reason": "approved", "expires": "2999-01-01"}]
    )
    assert report.overall_severity == Severity.ACKNOWLEDGED
    assert report.passed is True
    assert report.status_label == "COMPLIANT (WITH EXCEPTIONS)"
    finding = report.results[0].findings[0]
    assert finding.category == "WAIVER" and "WAIVED" in finding.message


def test_expired_waiver_rebreaches():
    report = _run_with_waivers(
        [{"rule": "SEC", "subject": "Government", "reason": "lapsed", "expires": "2000-01-01"}]
    )
    assert report.overall_severity == Severity.BREACH
    assert "waiver EXPIRED" in report.results[0].findings[0].message


def test_stale_waiver_is_flagged():
    report = _run_with_waivers(
        [{"rule": "SEC", "subject": "Financials", "reason": "no match", "expires": "2999-01-01"}]
    )
    # The Government breach stands; a stale-waiver note is added as INFO.
    assert report.overall_severity == Severity.BREACH
    notes = [f for f in report.results[0].findings if f.category == "WAIVER"]
    assert notes and "possibly stale" in notes[0].message


def test_waiver_without_subject_covers_whole_rule():
    report = _run_with_waivers([{"rule": "SEC", "reason": "blanket approval"}])
    assert report.overall_severity == Severity.ACKNOWLEDGED


@pytest.mark.parametrize(
    "waiver",
    [
        {"rule": "NOPE", "reason": "x"},                       # unknown rule id
        {"rule": "SEC"},                                       # missing reason
        {"rule": "SEC", "reason": "x", "expires": "not-a-date"},
    ],
)
def test_invalid_waivers_rejected(waiver):
    with pytest.raises(ValueError):
        ComplianceEngine.from_config(dict(_WAIVED_GUIDELINES, waivers=[waiver]))


def test_waiver_dataclass_from_config_defaults():
    w = Waiver.from_config({"rule": "R", "reason": "why"})
    assert w.subject is None and w.expires is None and w.approved_by is None

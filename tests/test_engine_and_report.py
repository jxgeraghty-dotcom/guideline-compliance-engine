"""Tests for the engine, report model and renderers."""

from __future__ import annotations

import json

import pytest

from compliance.engine import ComplianceEngine
from compliance.models import Portfolio, Severity
from compliance.report import render_html, render_json, render_text
from compliance.rules.base import Rule, register_rule
from conftest import pos

GUIDELINES = {
    "guidelines": [
        {
            "id": "ISS-01",
            "type": "issuer_concentration",
            "max_weight": 0.05,
            "exempt_sectors": ["Government"],
        },
        {"id": "SEC-01", "type": "sector_cap", "max_weight": 0.25},
        {"id": "DUR-01", "type": "duration_band", "min_duration": 3.0, "max_duration": 7.0},
        {"id": "CR-01", "type": "credit_floor", "min_rating": "BBB-", "max_below_weight": 0.0},
    ]
}


def test_engine_runs_all_rules(simple_portfolio):
    engine = ComplianceEngine.from_config(GUIDELINES)
    report = engine.run(simple_portfolio)
    assert len(report.results) == 4
    # This portfolio breaches issuer, sector and credit-floor rules.
    assert report.overall_severity == Severity.BREACH
    assert report.passed is False
    assert report.breach_count() >= 1


def test_engine_rejects_empty_guidelines():
    with pytest.raises(ValueError):
        ComplianceEngine.from_config({"guidelines": []})


def test_engine_rejects_duplicate_ids():
    config = {
        "guidelines": [
            {"id": "DUP", "type": "sector_cap", "max_weight": 0.25},
            {"id": "DUP", "type": "sector_cap", "max_weight": 0.25},
        ]
    }
    with pytest.raises(ValueError):
        ComplianceEngine.from_config(config)


def test_engine_captures_rule_exception():
    """A rule that raises becomes a data/error breach, not a crash."""

    @register_rule
    class _Exploding(Rule):
        rule_type = "_exploding_test_rule"

        def evaluate(self, portfolio):  # noqa: ARG002
            raise RuntimeError("boom")

    engine = ComplianceEngine([_Exploding({"id": "X"})])
    report = engine.run(Portfolio(name="p", positions=[pos("A", "A", 1)]))
    assert report.overall_severity == Severity.BREACH
    finding = report.results[0].findings[0]
    assert finding.category == "ERROR" and "boom" in finding.message


def test_render_json_roundtrips(simple_portfolio):
    engine = ComplianceEngine.from_config(GUIDELINES)
    report = engine.run(simple_portfolio)
    payload = json.loads(render_json(report))
    assert payload["passed"] is False
    assert payload["summary"]["rules_evaluated"] == 4
    assert {r["rule_id"] for r in payload["results"]} == {"ISS-01", "SEC-01", "DUR-01", "CR-01"}


def test_render_text_plain_has_no_ansi(simple_portfolio):
    engine = ComplianceEngine.from_config(GUIDELINES)
    report = engine.run(simple_portfolio)
    text = render_text(report, color=False)
    assert "\033[" not in text
    assert "NON-COMPLIANT" in text
    assert "ISS-01" in text


def test_render_html_is_self_contained(simple_portfolio):
    engine = ComplianceEngine.from_config(GUIDELINES)
    report = engine.run(simple_portfolio)
    html = render_html(report)
    assert html.startswith("<!DOCTYPE html>")
    assert "NON-COMPLIANT" in html
    assert "Alpha Corp" in html  # a breaching issuer surfaces in the report

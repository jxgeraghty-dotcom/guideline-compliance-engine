"""Guideline & compliance monitoring engine.

A small, dependency-light rules engine that evaluates an investment portfolio
against IMA-style guidelines (issuer concentration, credit floors, duration
bands, sector caps) and produces a severity-tagged compliance report.
"""

from compliance.engine import ComplianceEngine
from compliance.models import Portfolio, Position, Severity
from compliance.report import ComplianceReport
from compliance.rules.base import Finding, Rule, RuleResult, available_rule_types, create_rule

__version__ = "0.1.0"

__all__ = [
    "ComplianceEngine",
    "ComplianceReport",
    "Finding",
    "Portfolio",
    "Position",
    "Rule",
    "RuleResult",
    "Severity",
    "available_rule_types",
    "create_rule",
    "__version__",
]

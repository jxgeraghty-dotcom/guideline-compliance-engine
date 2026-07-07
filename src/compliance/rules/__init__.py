"""Rule implementations and the rule registry.

Importing this package registers every built-in rule type with the registry in
:mod:`compliance.rules.base`, so that :func:`compliance.rules.base.create_rule`
can build a rule from a guideline config dict by its ``type`` string.
"""

from compliance.rules import (
    credit_floor,
    duration_band,
    issuer_concentration,
    sector_cap,
)
from compliance.rules.base import (
    Finding,
    Rule,
    RuleResult,
    available_rule_types,
    create_rule,
    register_rule,
)

__all__ = [
    "Finding",
    "Rule",
    "RuleResult",
    "available_rule_types",
    "create_rule",
    "register_rule",
    "credit_floor",
    "duration_band",
    "issuer_concentration",
    "sector_cap",
]

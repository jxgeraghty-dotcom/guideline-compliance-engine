"""Rule framework: findings, results, the base ``Rule`` and the registry.

A *rule* encapsulates one guideline. It is constructed from a config dict and,
given a portfolio, returns a :class:`RuleResult` — a verdict plus zero or more
:class:`Finding` objects describing exactly what tripped (or nearly tripped)
the guideline.

New rule types register themselves with :func:`register_rule`, so the engine
can instantiate them purely from configuration without importing them directly.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from compliance.models import Portfolio, Severity


@dataclass
class Finding:
    """A single observation produced by a rule.

    ``observed`` and ``limit`` are optional numeric context (e.g. an observed
    weight and its cap) used for reporting; ``category`` distinguishes ordinary
    guideline findings from data-quality flags (``"DATA"``).
    """

    subject: str
    message: str
    severity: Severity
    observed: float | None = None
    limit: float | None = None
    metric: str | None = None
    category: str = "GUIDELINE"

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "message": self.message,
            "severity": self.severity.name,
            "observed": self.observed,
            "limit": self.limit,
            "metric": self.metric,
            "category": self.category,
        }


@dataclass
class RuleResult:
    """The outcome of evaluating one rule against a portfolio."""

    rule_id: str
    rule_type: str
    description: str
    findings: list[Finding] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def severity(self) -> Severity:
        """The worst severity across findings (``PASS`` when there are none)."""
        if not self.findings:
            return Severity.PASS
        return max(f.severity for f in self.findings)

    @property
    def passed(self) -> bool:
        return self.severity < Severity.BREACH

    def breaches(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == Severity.BREACH]

    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == Severity.WARN]

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "rule_type": self.rule_type,
            "description": self.description,
            "severity": self.severity.name,
            "passed": self.passed,
            "metrics": self.metrics,
            "findings": [f.to_dict() for f in self.findings],
        }


class Rule(ABC):
    """Base class for all guideline rules.

    Subclasses set the class attribute ``rule_type`` (the string used in config
    files) and implement :meth:`evaluate`. Common config keys (``id``,
    ``description``) are parsed here.
    """

    #: The ``type`` string that selects this rule in a guideline config.
    rule_type: str = ""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.rule_id: str = str(config.get("id") or self._default_id())
        self.description: str = str(config.get("description") or self.rule_id)

    def _default_id(self) -> str:
        return self.rule_type.upper().replace("_", "-")

    def _get_number(self, key: str, default: float | None = None) -> float | None:
        """Fetch a numeric config value, validating its type."""
        if key not in self.config or self.config[key] is None:
            return default
        value = self.config[key]
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(
                f"Rule {self.rule_id!r}: config key {key!r} must be a number, "
                f"got {value!r}."
            )
        return float(value)

    def _require_number(self, key: str) -> float:
        value = self._get_number(key)
        if value is None:
            raise ValueError(
                f"Rule {self.rule_id!r} ({self.rule_type}) requires config key {key!r}."
            )
        return value

    def _number(self, key: str, default: float) -> float:
        """Fetch a numeric config value with a concrete (non-None) default."""
        value = self._get_number(key, default)
        return default if value is None else value

    def _new_result(self, findings: list[Finding], metrics: dict[str, Any]) -> RuleResult:
        return RuleResult(
            rule_id=self.rule_id,
            rule_type=self.rule_type,
            description=self.description,
            findings=findings,
            metrics=metrics,
        )

    @abstractmethod
    def evaluate(self, portfolio: Portfolio) -> RuleResult:
        """Assess ``portfolio`` and return a :class:`RuleResult`."""
        raise NotImplementedError


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #

RULE_REGISTRY: dict[str, type[Rule]] = {}


def register_rule(cls: type[Rule]) -> type[Rule]:
    """Class decorator that registers a rule under its ``rule_type``."""
    key = cls.rule_type
    if not key:
        raise ValueError(f"{cls.__name__} must define a non-empty 'rule_type'.")
    if key in RULE_REGISTRY and RULE_REGISTRY[key] is not cls:
        raise ValueError(f"Duplicate rule_type {key!r} registered by {cls.__name__}.")
    RULE_REGISTRY[key] = cls
    return cls


def available_rule_types() -> list[str]:
    """Sorted list of registered rule type strings."""
    return sorted(RULE_REGISTRY)


def create_rule(config: dict[str, Any]) -> Rule:
    """Instantiate a rule from a guideline config dict.

    The dict must contain a ``type`` key matching a registered rule type.
    """
    if "type" not in config:
        raise ValueError(f"Guideline config is missing a 'type' key: {config!r}")
    rule_type = config["type"]
    if rule_type not in RULE_REGISTRY:
        known = ", ".join(available_rule_types()) or "(none)"
        raise ValueError(
            f"Unknown rule type {rule_type!r}. Registered types: {known}."
        )
    return RULE_REGISTRY[rule_type](config)

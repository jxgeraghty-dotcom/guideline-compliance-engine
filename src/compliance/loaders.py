"""Loaders for portfolios (CSV/JSON) and guideline documents (YAML/JSON).

Kept separate from the domain model so the model stays I/O-free. All loaders
raise :class:`LoaderError` with a clear message on malformed input, rather than
leaking low-level parser errors.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Sequence
from dataclasses import fields as dataclass_fields
from pathlib import Path
from typing import Any

from compliance.models import Portfolio, Position, Severity
from compliance.validation import reject_unknown_keys

#: Recognised top-level keys of a batch manifest.
_MANIFEST_KEYS = frozenset({"accounts", "name", "description", "metadata"})

#: Recognised top-level keys of a JSON portfolio document.
_PORTFOLIO_DOCUMENT_KEYS = frozenset(
    {"name", "base_currency", "as_of", "fx_rates", "positions", "metadata"}
)

#: Recognised keys of a JSON position entry: the Position fields themselves,
#: plus a free-form ``metadata`` escape hatch. Derived from the dataclass so
#: the schema can never drift from the model.
_POSITION_KEYS = frozenset(f.name for f in dataclass_fields(Position)) | {"metadata"}


class LoaderError(Exception):
    """Raised when input data cannot be parsed into the domain model."""


def _reject_unknown(context: str, mapping: dict[str, Any], allowed: frozenset[str]) -> None:
    """Strict key validation, surfaced as a :class:`LoaderError`."""
    try:
        reject_unknown_keys(context, mapping, allowed)
    except ValueError as exc:
        raise LoaderError(str(exc)) from exc


# Accepted CSV headers -> Position field. Extra columns are ignored.
_CSV_ALIASES = {
    "security_id": "security_id",
    "security": "security_id",
    "id": "security_id",
    "cusip": "security_id",
    "isin": "security_id",
    "ticker": "security_id",
    "issuer": "issuer",
    "issuer_name": "issuer",
    "name": "issuer",
    "market_value": "market_value",
    "marketvalue": "market_value",
    "mv": "market_value",
    "value": "market_value",
    "sector": "sector",
    "asset_class": "asset_class",
    "assetclass": "asset_class",
    "rating": "rating",
    "credit_rating": "rating",
    "rating_sp": "rating_sp",
    "sp": "rating_sp",
    "s&p": "rating_sp",
    "rating_moody": "rating_moody",
    "rating_moodys": "rating_moody",
    "moody": "rating_moody",
    "moodys": "rating_moody",
    "rating_fitch": "rating_fitch",
    "fitch": "rating_fitch",
    "duration": "duration",
    "effective_duration": "duration",
    "currency": "currency",
    "ccy": "currency",
    "ultimate_parent": "ultimate_parent",
    "parent": "ultimate_parent",
    "ultimate_parent_name": "ultimate_parent",
    "guarantor": "ultimate_parent",
    "instrument_type": "instrument_type",
    "instrument": "instrument_type",
    "notional": "notional",
    "notional_value": "notional",
    "notional_amount": "notional",
    "underlying_issuer": "underlying_issuer",
    "reference_entity": "underlying_issuer",
    "underlying": "underlying_issuer",
    "underlying_sector": "underlying_sector",
}


def load_portfolio(
    path: str | Path,
    *,
    name: str | None = None,
    base_currency: str | None = None,
    as_of: str | None = None,
    fx_rates: dict[str, float] | None = None,
) -> Portfolio:
    """Load a portfolio from a ``.csv`` or ``.json`` file."""
    path = Path(path)
    if not path.exists():
        raise LoaderError(f"Portfolio file not found: {path}")
    suffix = path.suffix.lower()
    if suffix == ".csv":
        portfolio = _load_portfolio_csv(path)
    elif suffix == ".json":
        portfolio = _load_portfolio_json(path)
    else:
        raise LoaderError(
            f"Unsupported portfolio format {suffix!r}; expected .csv or .json."
        )
    if name:
        portfolio.name = name
    elif not portfolio.name:
        portfolio.name = path.stem
    if base_currency:
        portfolio.base_currency = base_currency
    if as_of:
        portfolio.as_of = as_of
    if fx_rates:
        portfolio.fx_rates.update(normalize_fx_rates(fx_rates))
    return portfolio


def normalize_fx_rates(raw: dict[str, Any]) -> dict[str, float]:
    """Upper-case currency codes and coerce rates to float."""
    rates: dict[str, float] = {}
    for ccy, rate in raw.items():
        try:
            rates[str(ccy).strip().upper()] = float(rate)
        except (TypeError, ValueError) as exc:
            raise LoaderError(f"Invalid FX rate for {ccy!r}: {rate!r}.") from exc
    return rates


def _load_portfolio_csv(path: Path) -> Portfolio:
    positions: list[Position] = []
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            raise LoaderError(f"Portfolio CSV {path} has no header row.")
        field_map = _map_headers(reader.fieldnames, path)
        for line_no, row in enumerate(reader, start=2):
            record = {
                field_map[h]: v for h, v in row.items() if h in field_map and v is not None
            }
            positions.append(_build_position(record, f"{path}:{line_no}"))
    return Portfolio(name=path.stem, positions=positions)


def _map_headers(fieldnames: Sequence[str], path: Path) -> dict[str, str]:
    field_map: dict[str, str] = {}
    for header in fieldnames:
        key = header.strip().lower().replace(" ", "_")
        if key in _CSV_ALIASES:
            field_map[header] = _CSV_ALIASES[key]
    for required in ("security_id", "issuer", "market_value"):
        if required not in field_map.values():
            raise LoaderError(
                f"Portfolio CSV {path} is missing a required column for "
                f"{required!r}. Found: {', '.join(fieldnames)}."
            )
    return field_map


def _load_portfolio_json(path: Path) -> Portfolio:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise LoaderError(f"Portfolio JSON {path} is invalid: {exc}") from exc
    if isinstance(data, list):
        raw_positions = data
        meta: dict[str, Any] = {}
    elif isinstance(data, dict):
        _reject_unknown(f"Portfolio JSON {path.name}", data, _PORTFOLIO_DOCUMENT_KEYS)
        raw_positions = data.get("positions", [])
        meta = data
    else:
        raise LoaderError(f"Portfolio JSON {path} must be an object or a list.")
    positions = []
    for i, p in enumerate(raw_positions):
        where = f"{path}[{i}]"
        if not isinstance(p, dict):
            raise LoaderError(f"{where}: position entry must be a mapping.")
        _reject_unknown(where, p, _POSITION_KEYS)
        positions.append(_build_position(p, where))
    fx_rates = normalize_fx_rates(meta.get("fx_rates") or {})
    return Portfolio(
        name=str(meta.get("name") or path.stem),
        positions=positions,
        base_currency=str(meta.get("base_currency", "USD")),
        as_of=meta.get("as_of"),
        fx_rates=fx_rates,
    )


def _build_position(record: dict[str, Any], where: str) -> Position:
    def _opt(key: str) -> str | None:
        value = record.get(key)
        if value is None:
            return None
        return str(value).strip() or None

    def _req(key: str, default: str) -> str:
        return _opt(key) or default

    try:
        market_value = float(record["market_value"])
    except (KeyError, TypeError, ValueError) as exc:
        raise LoaderError(f"{where}: invalid or missing market_value.") from exc

    def _float(key: str) -> float | None:
        value = record.get(key)
        if value is None or value == "":
            return None
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise LoaderError(f"{where}: invalid {key} {value!r}.") from exc

    duration_val = _float("duration")
    notional_val = _float("notional")

    security_id = _opt("security_id")
    issuer = _opt("issuer")
    if not security_id or not issuer:
        raise LoaderError(f"{where}: security_id and issuer are required.")

    try:
        return Position(
            security_id=security_id,
            issuer=issuer,
            market_value=market_value,
            sector=_req("sector", "Unclassified"),
            asset_class=_req("asset_class", "Fixed Income"),
            rating=_opt("rating"),
            duration=duration_val,
            currency=_req("currency", "USD"),
            ultimate_parent=_opt("ultimate_parent"),
            instrument_type=_req("instrument_type", "bond"),
            notional=notional_val,
            underlying_issuer=_opt("underlying_issuer"),
            underlying_sector=_opt("underlying_sector"),
            rating_sp=_opt("rating_sp"),
            rating_moody=_opt("rating_moody"),
            rating_fitch=_opt("rating_fitch"),
        )
    except ValueError as exc:
        raise LoaderError(f"{where}: {exc}") from exc


def load_guidelines(path: str | Path) -> dict[str, Any]:
    """Load a guideline document from a ``.yaml``/``.yml`` or ``.json`` file."""
    path = Path(path)
    if not path.exists():
        raise LoaderError(f"Guidelines file not found: {path}")
    suffix = path.suffix.lower()
    text = path.read_text(encoding="utf-8")
    if suffix in (".yaml", ".yml"):
        data = _parse_yaml(text, path)
    elif suffix == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise LoaderError(f"Guidelines JSON {path} is invalid: {exc}") from exc
    else:
        raise LoaderError(
            f"Unsupported guidelines format {suffix!r}; expected .yaml, .yml or .json."
        )
    if not isinstance(data, dict):
        raise LoaderError(f"Guidelines file {path} must define a mapping at the top level.")
    return data


def _parse_yaml(text: str, path: Path) -> Any:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise LoaderError(
            "PyYAML is required to read YAML guideline files. Install it with "
            "'pip install PyYAML', or use a .json guidelines file instead."
        ) from exc
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise LoaderError(f"Guidelines YAML {path} is invalid: {exc}") from exc


def load_report_json(path: str | Path) -> dict[str, Any]:
    """Load a prior report JSON (as produced by ``-f json``) for comparison."""
    path = Path(path)
    if not path.exists():
        raise LoaderError(f"Baseline report not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise LoaderError(f"Baseline report {path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or "results" not in data:
        raise LoaderError(
            f"Baseline report {path} does not look like a report JSON (missing "
            f"'results'). Generate one with '-f json -o baseline.json'."
        )
    _validate_baseline_results(data["results"], path)
    return data


def _validate_baseline_results(results: Any, path: Path) -> None:
    """Check the shape the comparison relies on, so a hand-edited or truncated
    baseline fails with a clear message instead of a KeyError mid-diff."""
    if not isinstance(results, list):
        raise LoaderError(f"Baseline report {path}: 'results' must be a list.")
    severities = set(Severity.__members__)
    for i, result in enumerate(results):
        where = f"Baseline report {path}: results[{i}]"
        if not isinstance(result, dict):
            raise LoaderError(f"{where} must be a mapping.")
        if not isinstance(result.get("rule_id"), str):
            raise LoaderError(f"{where} is missing a 'rule_id' string.")
        if result.get("severity") not in severities:
            raise LoaderError(
                f"{where} ({result['rule_id']}) has an invalid 'severity' "
                f"{result.get('severity')!r}; expected one of {sorted(severities)}."
            )
        findings = result.get("findings")
        if not isinstance(findings, list):
            raise LoaderError(f"{where} ({result['rule_id']}) is missing a 'findings' list.")
        for j, finding in enumerate(findings):
            if (
                not isinstance(finding, dict)
                or not isinstance(finding.get("subject"), str)
                or finding.get("severity") not in severities
            ):
                raise LoaderError(
                    f"{where} ({result['rule_id']}) finding [{j}] needs a 'subject' "
                    f"string and a valid 'severity'."
                )


def load_name_list(path: str | Path) -> list[str]:
    """Read a restricted/name list: one name per line; ``#`` comments and blanks
    are ignored."""
    path = Path(path)
    if not path.exists():
        raise LoaderError(f"Name list file not found: {path}")
    names: list[str] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            names.append(stripped)
    return names


def resolve_restricted_lists(guidelines: dict[str, Any], base_dir: Path) -> None:
    """Inline any restricted_list ``file`` (relative to ``base_dir``) into ``names``.

    Called by the CLI so a guideline document can reference an external name list
    by a path relative to itself, while the rule stays free of I/O.
    """
    for entry in guidelines.get("guidelines", []):
        if not isinstance(entry, dict) or entry.get("type") != "restricted_list":
            continue
        file = entry.get("file")
        if not file:
            continue
        resolved = Path(file)
        if not resolved.is_absolute():
            resolved = base_dir / resolved
        names = list(entry.get("names") or [])
        names.extend(load_name_list(resolved))
        entry["names"] = names
        entry["file"] = None


def load_manifest(path: str | Path) -> dict[str, Any]:
    """Load a batch manifest (``.yaml``/``.yml`` or ``.json``).

    A manifest is a mapping with an ``accounts`` list; each account has a
    ``portfolio`` and ``guidelines`` path (and optional ``name``/``baseline``),
    resolved by the batch runner relative to the manifest file.
    """
    path = Path(path)
    if not path.exists():
        raise LoaderError(f"Manifest file not found: {path}")
    suffix = path.suffix.lower()
    text = path.read_text(encoding="utf-8")
    if suffix in (".yaml", ".yml"):
        data = _parse_yaml(text, path)
    elif suffix == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise LoaderError(f"Manifest JSON {path} is invalid: {exc}") from exc
    else:
        raise LoaderError(
            f"Unsupported manifest format {suffix!r}; expected .yaml, .yml or .json."
        )
    if not isinstance(data, dict) or not isinstance(data.get("accounts"), list):
        raise LoaderError(f"Manifest {path} must contain an 'accounts' list.")
    _reject_unknown(f"Manifest {path.name}", data, _MANIFEST_KEYS)
    return data

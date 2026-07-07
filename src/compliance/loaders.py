"""Loaders for portfolios (CSV/JSON) and guideline documents (YAML/JSON).

Kept separate from the domain model so the model stays I/O-free. All loaders
raise :class:`LoaderError` with a clear message on malformed input, rather than
leaking low-level parser errors.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from compliance.models import Portfolio, Position


class LoaderError(Exception):
    """Raised when input data cannot be parsed into the domain model."""


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


def _map_headers(fieldnames: list[str], path: Path) -> dict[str, str]:
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
        raw_positions = data.get("positions", [])
        meta = data
    else:
        raise LoaderError(f"Portfolio JSON {path} must be an object or a list.")
    positions = [
        _build_position(p, f"{path}[{i}]") for i, p in enumerate(raw_positions)
    ]
    fx_rates = normalize_fx_rates(meta.get("fx_rates") or {})
    return Portfolio(
        name=str(meta.get("name") or path.stem),
        positions=positions,
        base_currency=str(meta.get("base_currency", "USD")),
        as_of=meta.get("as_of"),
        fx_rates=fx_rates,
    )


def _build_position(record: dict[str, Any], where: str) -> Position:
    def _str(key: str, default: str | None = None) -> str | None:
        value = record.get(key, default)
        if value is None:
            return default
        value = str(value).strip()
        return value or default

    try:
        market_value = float(record["market_value"])
    except (KeyError, TypeError, ValueError) as exc:
        raise LoaderError(f"{where}: invalid or missing market_value.") from exc

    def _float(key: str) -> float | None:
        value = record.get(key)
        if value in ("", None):
            return None
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise LoaderError(f"{where}: invalid {key} {value!r}.") from exc

    duration_val = _float("duration")
    notional_val = _float("notional")

    security_id = _str("security_id")
    issuer = _str("issuer")
    if not security_id or not issuer:
        raise LoaderError(f"{where}: security_id and issuer are required.")

    try:
        return Position(
            security_id=security_id,
            issuer=issuer,
            market_value=market_value,
            sector=_str("sector", "Unclassified"),
            asset_class=_str("asset_class", "Fixed Income"),
            rating=_str("rating"),
            duration=duration_val,
            currency=_str("currency", "USD"),
            ultimate_parent=_str("ultimate_parent"),
            instrument_type=_str("instrument_type", "bond"),
            notional=notional_val,
            underlying_issuer=_str("underlying_issuer"),
            underlying_sector=_str("underlying_sector"),
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

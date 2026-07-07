# Guideline & Compliance Monitoring Engine

A small, dependency-light rules engine that checks an investment portfolio
against **IMA-style investment guidelines** and produces a **severity-tagged
compliance report**. It models the kind of pre-trade / post-trade guideline
monitoring an asset manager runs against a client's Investment Management
Agreement (IMA): issuer concentration, credit-quality floors, duration bands
and sector caps.

The tool is designed to slot into a workflow the way a real control would: run
it in a terminal for a formatted report, emit JSON for a downstream system, or
wire it into CI/pre-trade checks where a **non-zero exit code blocks on a
breach**.

Beyond the four core rules it also handles the details that make guideline
monitoring realistic: **ultimate-parent aggregation** (measure a limit across
a banking group's issuing entities), **multi-currency exposure** (FX-convert
holdings to base currency and cap foreign-currency risk), **derivatives
look-through** (attribute a CDS or future's notional to its underlying
issuer/sector/duration, not its small market value), and **period-over-period
comparison** against a prior report ("what newly breached since last night?").

```text
==============================================================================
  GUIDELINE COMPLIANCE REPORT
==============================================================================
  Portfolio : USD Investment Grade Aggregate — Account 10042
  Market val: USD 100,000,000   (21 positions)
  STATUS    : NON-COMPLIANT
  Summary   : 3 breach(es), 2 warning(s) across 4 rule(s)
------------------------------------------------------------------------------
[BREACH] ISSUER-CONC-01
         No more than 5% of market value in any single non-government issuer.
         ! JPMorgan Chase is 6.00% of the portfolio, over the 5.00% issuer limit (+1.00%).
         ! Bank of America is 4.50% of the portfolio, approaching the 5.00% issuer limit.

[BREACH] CREDIT-FLOOR-01
         Minimum issue rating of BBB-; up to 5% may be held below investment grade.
         ! 7.00% below the BBB- floor exceeds the 5.00% allowance (+2.00%): ...
         ! [DATA] MERIDPP01 (Meridian Logistics) is unrated; cannot verify against the BBB- floor.

[ PASS ] DURATION-BAND-01
         Portfolio effective duration within the 3.0-7.0 year band (benchmark +/- 2 yrs).
         effective duration 5.68 yrs within 3.00-7.00 yrs

[BREACH] SECTOR-CAP-01
         Max 25% per sector; Financials capped at 20%, Government permitted to 55%.
         ! Financials is 21.50% of the portfolio, over the 20.00% sector cap (+1.50%).
==============================================================================
```

## Why this exists

Guideline monitoring sits at the intersection of portfolio construction and
governance/stewardship. This engine keeps the **guidelines as data** (a YAML or
JSON IMA definition) and the **checks as small, testable rules**, so a
compliance analyst can express a mandate without touching Python and an engineer
can add a new check without touching the mandate. The severity model
(`PASS → INFO → WARN → BREACH`) mirrors how a monitoring desk triages: hard
breaches escalate, soft/near-limit conditions get watched, and data-quality
gaps ("this bond is unrated, we cannot certify it") are surfaced rather than
silently ignored.

## Install

```bash
python -m pip install -e .          # editable install exposes `compliance-check`
# or, to also install the test tooling:
python -m pip install -e ".[dev]"
```

Requires Python 3.10+. The only runtime dependency is **PyYAML** (used for YAML
guideline files; JSON guidelines work with no third-party dependencies at all).

## Quickstart

```bash
compliance-check check \
    --portfolio examples/portfolio.csv \
    --guidelines examples/guidelines.yaml

# machine-readable output for a downstream system
compliance-check check -p examples/portfolio.csv -g examples/guidelines.json -f json

# a shareable, self-contained HTML report
compliance-check check -p examples/portfolio.csv -g examples/guidelines.yaml \
    -f html -o compliance_report.html

# list the rule types the engine understands
compliance-check list-rules
```

If you have not installed the package, the same CLI is available via
`PYTHONPATH=src python -m compliance ...`.

### Exit codes (use it as a gate)

| Code | Meaning                                             |
|------|-----------------------------------------------------|
| `0`  | Compliant (subject to `--fail-on`)                  |
| `1`  | Guideline breach — or warning, if `--fail-on warn`  |
| `2`  | Usage or input error                                |

```bash
# fail a pre-trade / CI check on any breach (the default)
compliance-check check -p portfolio.csv -g ima.yaml || echo "BLOCKED: guideline breach"

# stricter: also block on warnings
compliance-check check -p portfolio.csv -g ima.yaml --fail-on warn
```

## Inputs

### Portfolio (`.csv` or `.json`)

CSV columns (header names are matched case-insensitively, with common aliases
such as `cusip`/`isin`/`ticker` → `security_id`, `mv`/`value` → `market_value`):

| Column          | Required | Notes                                             |
|-----------------|----------|---------------------------------------------------|
| `security_id`   | yes      | CUSIP/ISIN/ticker or any unique identifier        |
| `issuer`        | yes      | Used for issuer-concentration aggregation         |
| `market_value`  | yes      | Mark-to-market, in the position's own `currency`  |
| `sector`        | no       | Used for sector caps and issuer exemptions        |
| `asset_class`   | no       | Defaults to `Fixed Income`                        |
| `rating`        | no       | S&P/Fitch (`BBB-`) or Moody's (`Baa3`); `NR` = unrated |
| `duration`      | no       | Effective duration in years                       |
| `currency`      | no       | Defaults to `USD`; FX-converted to base via `fx_rates` |
| `ultimate_parent` | no     | Roll several issuing entities up to one parent    |
| `instrument_type` | no     | `bond` (default), `cds`, `future`, `swap`, `option`, … |
| `notional`      | no       | Economic exposure for a derivative (may be negative) |
| `underlying_issuer` | no   | Reference entity for look-through (e.g. a CDS name) |
| `underlying_sector` | no   | Reference sector for look-through                 |

JSON portfolios use
`{ "name", "base_currency", "as_of", "fx_rates": {...}, "positions": [ ... ] }`.

### Guidelines (`.yaml`, `.yml` or `.json`)

A guideline document is a mapping with a `guidelines` list; each entry is one
rule keyed by `type`. See [`examples/guidelines.yaml`](examples/guidelines.yaml).

## Rule catalogue

| `type`                  | Checks                                                        | Key config |
|-------------------------|--------------------------------------------------------------|------------|
| `issuer_concentration`  | Max weight in any single issuer or parent group             | `max_weight`, `warn_at`, `overrides`, `exempt_sectors`, `exempt_issuers`, `level`, `look_through` |
| `credit_floor`          | Minimum rating; policed below-floor (high-yield) bucket      | `min_rating`, `max_below_weight`, `warn_at`, `treat_unrated_as`, `look_through` |
| `duration_band`         | Portfolio effective duration within a `[min, max]` band      | `min_duration`, `max_duration`, `warn_buffer`, `look_through` |
| `sector_cap`            | Max (and optional min) weight per sector                     | `max_weight`, `overrides`, `floors`, `warn_ratio`, `look_through` |
| `currency_exposure`     | Cap per-currency and aggregate non-base exposure            | `max_per_currency`, `overrides`, `max_aggregate_foreign`, `warn_ratio` |

Every rule shares `id` and `description`. Numeric limits are **fractions**
(`0.05` = 5%). Highlights:

- **Issuer concentration** supports per-issuer `overrides` (a name with a
  higher limit) and `exempt_sectors` (e.g. sovereign debt carries no issuer
  cap). An issuer is only exempt if *all* of its holdings sit in exempt sectors.
- **Credit floor** handles both a hard floor (`max_below_weight: 0` — no holding
  below the floor) and a high-yield allowance (`max_below_weight: 0.05` — up to
  5% below the floor). Unrated holdings become a configurable data-quality flag
  (`treat_unrated_as: warn | breach | ignore`) because you cannot certify a
  rating you do not have. Both S&P/Fitch and Moody's notations are normalised
  onto one ordinal scale, and the report includes the market-value-weighted
  average rating.
- **Duration band** reports the MV-weighted effective duration, warns as it
  drifts within `warn_buffer` years of an edge, and flags fixed-income holdings
  that are missing a duration (which would understate the portfolio figure).
- **Sector cap** supports per-sector `overrides` (tighten Financials, loosen
  Government) and `floors` (minimum-weight mandates).
- **Currency exposure** caps any single non-base currency (`max_per_currency`,
  per-currency `overrides`) and the aggregate non-base weight
  (`max_aggregate_foreign`), all measured after FX conversion to base currency.

### Advanced controls

The [`examples/portfolio_multi.csv`](examples/portfolio_multi.csv) /
[`examples/guidelines_multi.yaml`](examples/guidelines_multi.yaml) pair exercises
all of these at once:

```bash
compliance-check check -p examples/portfolio_multi.csv -g examples/guidelines_multi.yaml
```

- **Ultimate-parent aggregation** — set `level: ultimate_parent` on an issuer
  rule and the limit is measured across every entity that shares an
  `ultimate_parent`, so "JPMorgan Chase Bank NA" and "JPMorgan Chase & Co" count
  as one 6% exposure to the group rather than two 3% names.
- **Multi-currency exposure** — supply `fx_rates` (in the guideline document or
  the portfolio JSON) mapping each currency to its value in base currency
  (`EUR: 1.08`). Weights, NAV and every limit are then computed in base terms.
  The engine refuses to run if a held currency has no rate, rather than silently
  mis-weighting the book.
- **Derivatives look-through** — set `look_through: true` on a rule and a
  derivative contributes its **notional** (attributed to `underlying_issuer` /
  `underlying_sector`) instead of its small mark. A single-name CDS then counts
  toward the reference entity's concentration and credit bucket; a bond future's
  notional counts toward duration.

## Comparing against a baseline (as-of / look-back)

Save one run as JSON, then compare a later run against it to see exactly what
changed — which rules newly breached, which cleared, and which specific
issuers/sectors appeared or dropped off:

```bash
# period 1 — snapshot the baseline
compliance-check check -p last_month.csv -g ima.yaml -f json -o baseline.json

# period 2 — today's book, with a "changes since baseline" section appended
compliance-check check -p today.csv -g ima.yaml --baseline baseline.json
```

```text
------------------------------------------------------------------------------
  CHANGES SINCE BASELINE  (as of 2026-06-30T..., was COMPLIANT)
  ^ NEW_BREACH   ISSUER-CONC-01  (PASS -> BREACH)
        + now flagged: JPMorgan Chase
  ^ NEW_BREACH   SECTOR-CAP-01   (PASS -> BREACH)
        + now flagged: Financials
```

The comparison is included in the JSON and HTML outputs too. It matches rules by
`id`, so keep guideline ids stable across periods.

## Architecture

```
src/compliance/
├── models.py      # Position, Portfolio, Severity — I/O-free model (FX + exposure)
├── ratings.py     # S&P/Moody's rating scale, notches, weighted-average rating
├── rules/
│   ├── base.py    # Rule ABC, Finding, RuleResult, and the type registry
│   ├── issuer_concentration.py   # + ultimate-parent, look-through
│   ├── credit_floor.py           # + look-through
│   ├── duration_band.py          # + look-through
│   ├── sector_cap.py             # + look-through
│   └── currency_exposure.py
├── engine.py      # builds rules from config, runs them, assembles the report
├── report.py      # ComplianceReport + text / JSON / HTML renderers
├── compare.py     # as-of / look-back diff against a prior report
├── loaders.py     # CSV/JSON portfolios (+ FX), YAML/JSON guidelines
└── cli.py         # argparse CLI, exit-code gating, --baseline comparison
```

Design choices worth calling out:

- **Guidelines are data, checks are code.** Rules self-register with a registry
  keyed by their `type` string, so the engine instantiates them purely from a
  config file — no rule imports in the mandate, no mandate logic in the rules.
- **The domain model has no I/O.** `Portfolio`/`Position`/`Severity` know
  nothing about files or reports, which keeps them trivial to test and reuse.
- **Severity rolls up by `max()`.** A rule's verdict is the worst of its
  findings; the report's status is the worst of its rules. Because `Severity`
  is an ordered `IntEnum`, that rollup is a one-liner.
- **The engine degrades gracefully.** A rule that raises is captured as an
  error-category breach rather than aborting the run — a monitoring engine
  should always produce a report and tell you what it could not evaluate.

### Adding a new rule

```python
from compliance.models import Portfolio, Severity
from compliance.rules.base import Finding, Rule, RuleResult, register_rule

@register_rule
class MaxCashRule(Rule):
    rule_type = "max_cash"                       # the `type:` used in config

    def __init__(self, config):
        super().__init__(config)
        self.max_weight = self._require_number("max_weight")

    def evaluate(self, portfolio: Portfolio) -> RuleResult:
        cash = portfolio.aggregate_weight(lambda p: p.asset_class).get("Cash", 0.0)
        findings = []
        if cash > self.max_weight:
            findings.append(Finding(
                subject="Cash", severity=Severity.BREACH,
                observed=cash, limit=self.max_weight,
                message=f"Cash is {cash:.1%}, over the {self.max_weight:.1%} limit.",
            ))
        return self._new_result(findings, {"cash_weight": cash})
```

Importing the module registers the rule; `type: max_cash` in a guideline file
then just works.

## Testing

```bash
python -m pytest
```

70 tests cover the rating scale, each rule (breach / warn / exemption / data
edge cases), FX conversion, ultimate-parent aggregation, derivatives
look-through, the currency rule, baseline comparison, the engine (rollup,
duplicate-id and error handling), the loaders, all three renderers, and the
CLI's exit-code behaviour. The test config puts `src/` on the path, so the
suite runs without an install.

## Scope & assumptions

- FX rates convert every holding to the portfolio's base currency; the engine
  requires a rate for each currency held. Cross-rates/triangulation are out of
  scope — supply direct base-currency rates.
- Cash instruments are long-only (negative marks are rejected); derivatives may
  carry a negative mark or a signed `notional` to represent a short/hedge.
- Ratings map onto the S&P/Fitch scale; Moody's grades are normalised in.
- Look-through uses a derivative's notional as its economic exposure (a
  first-order proxy); delta/DV01 adjustment is a natural refinement.

Further extension points: issuer *guarantor* look-through, option delta
weighting, benchmark-relative (active) limits, and scheduled runs that persist
each night's report as the next day's baseline.

## License

MIT.

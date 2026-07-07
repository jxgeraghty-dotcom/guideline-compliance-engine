# Project Status — Guideline & Compliance Monitoring Engine

A handoff-oriented snapshot of the project. For install/usage and the full rule
catalogue, see [README.md](README.md); for the version history, see
[CHANGELOG.md](CHANGELOG.md).

Repo: https://github.com/jxgeraghty-dotcom/guideline-compliance-engine
(public · default branch `main`) · Version `0.4.0` (tagged `v0.4.0`)

## Goal

Build a small, dependency-light **rules engine** that checks an investment
portfolio against **IMA-style investment guidelines** (issuer concentration,
credit-quality floors, duration bands, sector caps, and more) and produces a
**severity-tagged compliance report**. It models the pre-trade / post-trade
guideline monitoring an asset manager runs against a client's Investment
Management Agreement, and is meant to work three ways: a formatted terminal
report, machine-readable JSON for downstream systems, and a **CI / pre-trade
gate** (non-zero exit code on a breach). The project doubles as a competency
signal for governance/stewardship and GIPS/compliance work.

## Current state

Working, green, and published. The engine is feature-complete for the scope
defined so far.

- **Quality gates:** 129 tests passing; `ruff` clean; `mypy` clean (the package
  ships a `py.typed` marker). GitHub Actions CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml))
  runs lint + types + tests on Python 3.10–3.12 and asserts the exit-code gate.
- **Rules (6):** `issuer_concentration`, `credit_floor`, `duration_band`,
  `sector_cap`, `currency_exposure`, `restricted_list`.
- **Cross-cutting capabilities:** multi-currency exposure (FX to base,
  hedge-aware via currency look-through),
  derivatives look-through (notional → underlying), ultimate-parent
  aggregation, net/gross netting, waivers (approved exceptions →
  `ACKNOWLEDGED`, re-breach on expiry), multi-agency rating basis
  (lower/higher/median), as-of / look-back comparison against a prior report,
  and boundary-safe limit comparisons.
- **Safety:** strict config-key validation across the guideline document,
  every rule, every waiver, and the batch manifest — unknown keys fail loud
  with a "did you mean …?" suggestion (a `metadata:` block is allowed for
  annotations).
- **Interfaces:** CLI `check`, `check-batch`, `list-rules`; renderers for text
  (ANSI), JSON, and standalone HTML (single-report and batch dashboard).
- **History:** six commits, `b4174d9` (initial) → `e7c58d5` (manifest
  validation). Branch renamed `master` → `main`.

## Key decisions (and why)

- **Python, `src/` layout, dependency-light (only PyYAML).** Professional
  packaging signal; the JSON config/portfolio path needs no third-party deps.
- **Guidelines are data, checks are code.** Rules self-register in a type
  registry, so a mandate is authored in YAML/JSON without touching Python and a
  new rule is added without touching mandates.
- **Severity is an ordered `IntEnum`, rolled up by `max()`.**
  `PASS → INFO → ACKNOWLEDGED → WARN → BREACH`. `ACKNOWLEDGED` sits below `WARN`
  so a waived breach stays visible but does not trip the breach gate.
- **Look-through uses notional as a first-order exposure proxy**, with a
  `net`/`gross` switch to make hedge offsetting explicit (some mandates require
  gross).
- **FX uses direct base-currency rates and refuses to run if a held currency
  has no rate** — fail loud rather than silently mis-weight the book.
- **Boundary precision via an explicit documented tolerance (`1e-9`)** rather
  than `Decimal`, so a holding exactly on a limit is deterministic. (Kept
  floats for simplicity; see Open questions.)
- **Fail loud on unknown config keys everywhere.** For a control that fails
  safe on absence, a silently-mistyped optional key is a false negative — the
  worst failure mode — so typos raise instead of being ignored.
- **ASCII-safe text output + an encoding-tolerant CLI print**, so the tool
  never crashes on a limited console (e.g. Windows cp1252).
- **Isolated nested git repo.** The user's home directory is itself a Git repo,
  so the project got its own `git init` to avoid being absorbed.
- **Published as a public GitHub repo, default branch `main`**, to serve as a
  portfolio artifact.

## Open questions / unresolved

- **Not yet built** (candidates from review, all optional): `Decimal`-based
  money/weights (beyond the tolerance epsilon); **option delta weighting** for
  look-through; **benchmark-relative (active) limits**; scheduled runs that
  persist each night's report as the next day's baseline; issuer *guarantor*
  look-through; currency look-through for derivatives denominated differently
  from their underlying.
- **`median` rating basis for even counts** is defined as the worse of the two
  central notches ("median of three, lower of two"); reasonable, but worth
  confirming against a specific mandate's wording.
- **Top-level *manifest* is strict but its per-account schema is intentionally
  captured per-account** (a malformed account errors that account, not the
  batch). Confirm that resilience model is desired.

## Files / artifacts

**Package** — `src/compliance/`
- `models.py` — `Position`, `Portfolio`, `Severity` (I/O-free; FX + exposure).
- `ratings.py` — S&P/Moody's scale, notches, multi-agency effective rating.
- `tolerance.py` — boundary-safe comparison helpers.
- `validation.py` — strict config-key rejection with suggestions.
- `rules/` — `base.py` (Rule ABC + registry) and one module per rule:
  `issuer_concentration`, `credit_floor`, `duration_band`, `sector_cap`,
  `currency_exposure`, `restricted_list`.
- `waivers.py` — approved exceptions with expiry.
- `engine.py` — builds rules + waivers from config; runs them.
- `report.py` — `ComplianceReport` + text/JSON/HTML + batch renderers.
- `compare.py` — as-of / look-back diff. `batch.py` — book-of-accounts runner
  + shared single-account pipeline.
- `loaders.py` — CSV/JSON portfolios (+ FX), YAML/JSON guidelines, manifests.
- `cli.py` / `__main__.py` — argparse CLI + `python -m compliance`.

**Examples** — `examples/`
- Basic: `portfolio.csv`, `guidelines.yaml` / `.json`, `portfolio_prior.csv`
  (baseline comparison).
- Advanced: `portfolio_multi.csv`, `guidelines_multi.yaml` (FX, look-through,
  parent).
- Governance: `portfolio_credit.json`, `guidelines_credit.yaml`,
  `restricted_names.txt` (waivers, rating basis, restricted list).
- Batch: `accounts.yaml`.

**Tests** — `tests/` (129): `test_ratings`, `test_rules`,
`test_engine_and_report`, `test_loaders_and_cli`, `test_v2_features`,
`test_v2_loaders_cli`, `test_v3_features`, `test_v3_batch_cli`,
`test_config_validation`, `test_golden` (+ `golden/` snapshots), `conftest.py`.

**Project** — [`README.md`](README.md), [`CHANGELOG.md`](CHANGELOG.md),
[`pyproject.toml`](pyproject.toml), [`.github/workflows/ci.yml`](.github/workflows/ci.yml),
this `STATUS.md`. Generated reports land in the git-ignored `reports/`.

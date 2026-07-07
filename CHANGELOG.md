# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and the project adheres to
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **Hedge-aware currency exposure.** `currency_exposure` accepts
  `look_through: true`, measuring per-currency exposure by signed economic
  exposure so an FX forward (booked as a short-notional position in the hedged
  currency) nets measured exposure down — the "hedged exposure" basis most IMAs
  allow. An over-hedged currency nets short and still counts at its magnitude
  (flagged "net short"); `netting: gross` disables the offset for mandates that
  cap gross exposure.
- **Strict key validation for JSON portfolios.** A JSON portfolio document and
  each position entry now reject unknown keys with a "did you mean …?"
  suggestion (a `metadata` block stays free-form), closing the last gap in the
  fail-loud philosophy — a typo like `underlying_isuer` or a top-level
  `fx_rate` used to be silently ignored.
- **Deep baseline validation.** `--baseline` report JSONs are checked for the
  shape the comparison relies on (`rule_id`, `severity`, `findings`), so a
  hand-edited or truncated baseline fails with a clear input error (exit 2)
  instead of a `KeyError` crash.
- `LICENSE` file (MIT), matching the licence already declared in
  `pyproject.toml`.

### Fixed
- **Exit-code integrity.** An unexpected internal error in the CLI now exits
  with code 2 (input/usage error) instead of Python's default 1, which would
  have been indistinguishable from "breach found" to a CI gate. The traceback
  still prints to stderr.
- **Sector exemptions under look-through.** With `look_through: true`, the
  issuer-concentration `exempt_sectors` check now follows the underlying's
  sector (`risk_sector`) rather than the derivative contract's own book
  sector — a CDS referencing a sovereign is sovereign risk.
- **Duration coverage flag counts magnitudes.** A short overlay with no
  duration no longer offsets a long one in the missing-duration data-quality
  weight; uncovered exposure is measured by absolute size.

### Changed
- The version is now single-sourced from `compliance.__version__`
  (`pyproject.toml` reads it at build time), the `_pct` formatting helper is
  shared by all rules instead of being duplicated per file, `to_dict()` walks
  the findings once for its summary counts, and ruff now enforces the
  `E/F/W/I/B/UP/SIM` rule families (imports sorted, bug-prone patterns and
  legacy idioms linted) in CI.

## [0.3.0]

### Added
- **Waivers / approved exceptions.** A `waivers` block in the guideline document
  downgrades a matching finding to a new `ACKNOWLEDGED` severity while unexpired
  (documented with reason, approver and expiry); once past expiry the finding
  re-breaches automatically. Stale waivers (matching nothing) are flagged.
- **Multi-agency rating basis.** Positions accept `rating_sp` / `rating_moody` /
  `rating_fitch`; the credit-floor rule combines them via `rating_basis`
  (`lower` / `higher` / `median`, i.e. lower-of-two / median-of-three).
- **Restricted-list rule** (`restricted_list`) — screens issuer, ultimate parent
  and derivative reference entity against a sanctions/exclusion list (inline
  `names` or an external `file`).
- **Batch mode** — `check-batch` evaluates a manifest of accounts and prints a
  one-row-per-account summary (text / JSON / HTML); resilient to a single
  account failing to load.
- **Netting switch** (`net` / `gross`) on the issuer and sector rules, making
  hedge offsetting explicit for signed-derivative look-through.
- **Strict config-key validation.** The guideline document, every rule, every
  waiver and the batch manifest (top level and per account) declare their
  allowed keys; an unrecognised key raises with a "did you mean …?" suggestion
  instead of being silently ignored — so a typo like `look_throuh`, `expiry`,
  `fx_rate` or a manifest `guidlines` can no longer quietly disable a control.
  A free-form `metadata` block is allowed for annotations. A malformed manifest
  account is captured as that account's error, keeping the batch resilient.
- Engineering: `py.typed` marker, a mypy pass (clean), a GitHub Actions CI
  workflow that also demonstrates the exit-code gate, and golden-file snapshot
  tests for the text/HTML renderers.

### Changed
- All limit comparisons now go through an explicit, documented tolerance
  (`compliance.tolerance`), so a holding sitting exactly on a limit is
  deterministic and never flips on floating-point noise.

## [0.2.0]

### Added
- Ultimate-parent aggregation (`level: ultimate_parent`) for issuer limits.
- Multi-currency exposure: per-position currency + `fx_rates`; NAV/weights in
  base currency; a new `currency_exposure` rule. The run refuses to proceed if a
  held currency has no rate.
- Derivatives look-through (`look_through: true`) attributing a derivative's
  notional to its underlying issuer/sector/duration.
- As-of / look-back comparison against a prior report (`--baseline`), rendered
  in text, JSON and HTML.

## [0.1.0]

### Added
- Initial engine: four rules (issuer concentration, credit floor, duration band,
  sector cap), a `PASS/INFO/WARN/BREACH` severity model, CSV/JSON portfolio and
  YAML/JSON guideline loaders, text/JSON/HTML report renderers, and an argparse
  CLI with exit-code gating for pre-trade/CI use.

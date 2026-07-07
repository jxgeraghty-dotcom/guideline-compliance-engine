# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and the project adheres to
[Semantic Versioning](https://semver.org/).

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
- **Strict config-key validation.** Every rule (and every waiver) declares its
  allowed keys; an unrecognised key raises with a "did you mean …?" suggestion
  instead of being silently ignored — so a typo like `look_throuh` or `expiry`
  can no longer quietly disable a control.
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

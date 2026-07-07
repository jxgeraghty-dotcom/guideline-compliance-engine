"""The compliance report and its renderers (text, JSON, HTML).

:class:`ComplianceReport` is a pure data object holding the outcome of a run;
the ``render_*`` functions turn it into human- or machine-readable output. The
report is kept free of I/O so it is trivial to test and to embed elsewhere.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from html import escape
from typing import TYPE_CHECKING, Any, TextIO

from compliance.models import Severity
from compliance.rules.base import RuleResult

if TYPE_CHECKING:  # avoid a circular import; compare.py imports this module.
    from compliance.compare import ReportComparison


@dataclass
class ComplianceReport:
    """The result of evaluating a portfolio against a guideline set."""

    portfolio_name: str
    base_currency: str
    as_of: str | None
    generated_at: str
    total_market_value: float
    position_count: int
    results: list[RuleResult]

    @property
    def overall_severity(self) -> Severity:
        if not self.results:
            return Severity.PASS
        return max(r.severity for r in self.results)

    @property
    def passed(self) -> bool:
        return self.overall_severity < Severity.BREACH

    @property
    def status_label(self) -> str:
        return {
            Severity.PASS: "COMPLIANT",
            Severity.INFO: "COMPLIANT",
            Severity.WARN: "COMPLIANT (WITH WARNINGS)",
            Severity.BREACH: "NON-COMPLIANT",
        }[self.overall_severity]

    def counts(self) -> dict[str, int]:
        """Number of individual findings at each severity level."""
        result = {s.name: 0 for s in Severity}
        for r in self.results:
            for f in r.findings:
                result[f.severity.name] += 1
        return result

    def breach_count(self) -> int:
        """Count of BREACH findings across all rules (not rules)."""
        return self.counts()[Severity.BREACH.name]

    def warn_count(self) -> int:
        """Count of WARN findings across all rules (not rules)."""
        return self.counts()[Severity.WARN.name]

    def breached_rule_count(self) -> int:
        return sum(1 for r in self.results if r.severity == Severity.BREACH)

    def to_dict(self) -> dict[str, Any]:
        return {
            "portfolio_name": self.portfolio_name,
            "base_currency": self.base_currency,
            "as_of": self.as_of,
            "generated_at": self.generated_at,
            "total_market_value": self.total_market_value,
            "position_count": self.position_count,
            "overall_severity": self.overall_severity.name,
            "status": self.status_label,
            "passed": self.passed,
            "summary": {
                "rules_evaluated": len(self.results),
                "rules_breached": self.breached_rule_count(),
                "breaches": self.breach_count(),
                "warnings": self.warn_count(),
                "finding_counts": self.counts(),
            },
            "results": [r.to_dict() for r in self.results],
        }


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #

_ANSI = {
    Severity.PASS: "\033[32m",   # green
    Severity.INFO: "\033[36m",   # cyan
    Severity.WARN: "\033[33m",   # yellow
    Severity.BREACH: "\033[31m",  # red
}
_BOLD = "\033[1m"
_DIM = "\033[2m"
_RESET = "\033[0m"

_BADGE = {
    Severity.PASS: "PASS",
    Severity.INFO: "INFO",
    Severity.WARN: "WARN",
    Severity.BREACH: "BREACH",
}

# Comparison transitions -> (ASCII glyph, colour severity, css class).
# Glyphs are ASCII so the text report encodes cleanly on any console (Windows
# cp1252 included); direction is reinforced by colour and the transition label.
_TRANSITION_STYLE = {
    "NEW_BREACH": ("^", Severity.BREACH, "breach"),
    "WORSENED": ("^", Severity.BREACH, "breach"),
    "NEW_RULE": ("+", Severity.INFO, "pass"),
    "RESOLVED": ("v", Severity.PASS, "pass"),
    "IMPROVED": ("v", Severity.PASS, "pass"),
    "REMOVED_RULE": ("-", Severity.INFO, "pass"),
    "UNCHANGED": ("=", Severity.INFO, "pass"),
}


def _use_color(stream: TextIO, override: bool | None) -> bool:
    if override is not None:
        return override
    if os.environ.get("NO_COLOR") is not None:
        return False
    return bool(getattr(stream, "isatty", lambda: False)())


def _fmt_money(value: float, currency: str) -> str:
    return f"{currency} {value:,.0f}"


def render_text(
    report: ComplianceReport,
    *,
    color: bool | None = None,
    stream: TextIO | None = None,
    comparison: "ReportComparison | None" = None,
) -> str:
    """Render a coloured, human-readable report."""
    stream = stream or sys.stdout
    use_color = _use_color(stream, color)

    def paint(text: str, code: str) -> str:
        return f"{code}{text}{_RESET}" if use_color else text

    def badge(sev: Severity) -> str:
        return paint(f"[{_BADGE[sev]:^6}]", _ANSI[sev])

    width = 78
    lines: list[str] = []
    lines.append("=" * width)
    lines.append(paint("  GUIDELINE COMPLIANCE REPORT", _BOLD))
    lines.append("=" * width)
    lines.append(f"  Portfolio : {report.portfolio_name}")
    lines.append(
        f"  Market val: {_fmt_money(report.total_market_value, report.base_currency)}"
        f"   ({report.position_count} positions)"
    )
    if report.as_of:
        lines.append(f"  As of     : {report.as_of}")
    lines.append(f"  Generated : {report.generated_at}")

    overall = report.overall_severity
    status_line = f"  STATUS    : {report.status_label}"
    lines.append(paint(status_line, _BOLD + _ANSI[overall]))
    lines.append(
        f"  Summary   : {report.breach_count()} breach(es), "
        f"{report.warn_count()} warning(s) across {len(report.results)} rule(s)"
    )
    lines.append("-" * width)

    for result in report.results:
        lines.append(f"{badge(result.severity)} {paint(result.rule_id, _BOLD)}")
        lines.append(f"         {result.description}")
        for finding in _sorted_findings(result):
            marker = paint("!", _ANSI[finding.severity]) if use_color else "!"
            tag = f"[{finding.category}] " if finding.category not in ("GUIDELINE",) else ""
            lines.append(f"         {marker} {tag}{finding.message}")
        if result.severity == Severity.PASS and not result.findings:
            summary = _pass_summary(result)
            if summary:
                lines.append(paint(f"         {summary}", _DIM) if use_color else f"         {summary}")
        lines.append("")

    if comparison is not None:
        lines.extend(_render_comparison_text(comparison, paint, width))

    lines.append("=" * width)
    footer = f"  RESULT: {report.status_label}"
    lines.append(paint(footer, _BOLD + _ANSI[overall]))
    lines.append("=" * width)
    return "\n".join(lines)


def _render_comparison_text(comparison, paint, width: int) -> list[str]:
    lines = ["-" * width]
    lines.append(
        paint("  CHANGES SINCE BASELINE", _BOLD)
        + f"  (as of {comparison.baseline_label}, was {comparison.prior_status})"
    )
    changed = comparison.changed_rules()
    if not changed:
        lines.append("  No change in rule status since the baseline.")
        lines.append("")
        return lines
    for c in changed:
        glyph, sev, _ = _TRANSITION_STYLE[c.transition]
        prior = c.prior_severity or "-"
        current = c.current_severity or "-"
        head = f"  {glyph} {c.transition:<12} {c.rule_id}  ({prior} -> {current})"
        lines.append(paint(head, _ANSI[sev]))
        for subj in c.new_subjects:
            lines.append(f"        + now flagged: {subj}")
        for subj in c.resolved_subjects:
            lines.append(f"        - cleared:     {subj}")
    lines.append("")
    return lines


def _sorted_findings(result: RuleResult) -> list:
    return sorted(result.findings, key=lambda f: f.severity, reverse=True)


def _pass_summary(result: RuleResult) -> str | None:
    """A one-line reassurance for a clean rule, drawn from its metrics."""
    m = result.metrics
    if result.rule_type == "issuer_concentration" and m.get("largest_issuer"):
        return (
            f"largest issuer {m['largest_issuer']} at "
            f"{m['largest_issuer_weight'] * 100:.2f}% (limit {m['limit'] * 100:.2f}%)"
        )
    if result.rule_type == "duration_band":
        return (
            f"effective duration {m.get('portfolio_duration', 0):.2f} yrs within "
            f"{m.get('min_duration'):.2f}-{m.get('max_duration'):.2f} yrs"
        )
    if result.rule_type == "credit_floor":
        avg = m.get("weighted_average_rating")
        return f"weighted-average rating {avg}" if avg else None
    if result.rule_type == "sector_cap":
        weights = m.get("sector_weights") or {}
        if weights:
            top, w = next(iter(weights.items()))
            return f"largest sector {top} at {w * 100:.2f}% (cap {m['default_cap'] * 100:.2f}%)"
    return None


def render_json(
    report: ComplianceReport,
    *,
    indent: int = 2,
    comparison: "ReportComparison | None" = None,
) -> str:
    """Render the report as a JSON document."""
    payload = report.to_dict()
    if comparison is not None:
        payload["comparison"] = comparison.to_dict()
    return json.dumps(payload, indent=indent)


def render_html(
    report: ComplianceReport,
    *,
    comparison: "ReportComparison | None" = None,
) -> str:
    """Render a standalone, styled HTML report."""
    css_class = {
        Severity.PASS: "pass",
        Severity.INFO: "pass",
        Severity.WARN: "warn",
        Severity.BREACH: "breach",
    }
    overall_cls = css_class[report.overall_severity]

    rows: list[str] = []
    for result in report.results:
        cls = css_class[result.severity]
        finding_html = ""
        findings = _sorted_findings(result)
        if findings:
            items = "".join(
                f'<li class="{css_class[f.severity]}">'
                f'<span class="badge {css_class[f.severity]}">{_BADGE[f.severity]}</span>'
                f"{escape(f.message)}</li>"
                for f in findings
            )
            finding_html = f"<ul class='findings'>{items}</ul>"
        else:
            summary = _pass_summary(result)
            if summary:
                finding_html = f"<p class='summary'>{escape(summary)}</p>"
        rows.append(
            f"<tr class='{cls}'>"
            f"<td class='sev'><span class='badge {cls}'>{_BADGE[result.severity]}</span></td>"
            f"<td><div class='rid'>{escape(result.rule_id)}</div>"
            f"<div class='rdesc'>{escape(result.description)}</div>{finding_html}</td>"
            f"</tr>"
        )

    as_of = f"<div><span>As of</span>{escape(report.as_of)}</div>" if report.as_of else ""
    return _HTML_TEMPLATE.format(
        title=escape(report.portfolio_name),
        overall_cls=overall_cls,
        status=escape(report.status_label),
        portfolio=escape(report.portfolio_name),
        market_value=escape(_fmt_money(report.total_market_value, report.base_currency)),
        positions=report.position_count,
        breaches=report.breach_count(),
        warnings=report.warn_count(),
        rules=len(report.results),
        generated=escape(report.generated_at),
        as_of=as_of,
        rows="\n".join(rows),
        comparison=_render_comparison_html(comparison),
    )


def _render_comparison_html(comparison) -> str:
    if comparison is None:
        return ""
    changed = comparison.changed_rules()
    header = (
        f"<h2>Changes since baseline</h2>"
        f"<p class='baseline'>Baseline as of {escape(str(comparison.baseline_label))} "
        f"— was {escape(str(comparison.prior_status))}.</p>"
    )
    if not changed:
        return (
            f"<section class='changes'>{header}"
            f"<p class='summary'>No change in rule status since the baseline.</p></section>"
        )
    items = []
    for c in changed:
        _, _, cls = _TRANSITION_STYLE[c.transition]
        subjects = "".join(
            f"<li class='subj-new'>now flagged: {escape(s)}</li>" for s in c.new_subjects
        ) + "".join(
            f"<li class='subj-clear'>cleared: {escape(s)}</li>" for s in c.resolved_subjects
        )
        subj_html = f"<ul class='subjects'>{subjects}</ul>" if subjects else ""
        items.append(
            f"<li class='{cls}'>"
            f"<span class='badge {cls}'>{escape(c.transition.replace('_', ' '))}</span>"
            f"<span class='chg-rid'>{escape(c.rule_id)}</span>"
            f"<span class='chg-sev'>{escape(c.prior_severity or '—')} &rarr; "
            f"{escape(c.current_severity or '—')}</span>{subj_html}</li>"
        )
    return (
        f"<section class='changes'>{header}"
        f"<ul class='change-list'>{''.join(items)}</ul></section>"
    )


_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Compliance Report — {title}</title>
<style>
  :root {{
    --pass: #1a7f37; --pass-bg: #e6f4ea;
    --warn: #9a6700; --warn-bg: #fff4d6;
    --breach: #cf222e; --breach-bg: #ffe9e9;
    --ink: #1f2328; --muted: #656d76; --line: #d0d7de;
  }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    color: var(--ink); margin: 0; background: #f6f8fa; }}
  .wrap {{ max-width: 900px; margin: 0 auto; padding: 32px 20px 64px; }}
  h1 {{ font-size: 20px; margin: 0 0 4px; }}
  .banner {{ border-radius: 10px; padding: 18px 22px; margin: 18px 0 24px;
    border: 1px solid var(--line); font-weight: 700; font-size: 18px; }}
  .banner.pass {{ background: var(--pass-bg); color: var(--pass); }}
  .banner.warn {{ background: var(--warn-bg); color: var(--warn); }}
  .banner.breach {{ background: var(--breach-bg); color: var(--breach); }}
  .meta {{ display: flex; flex-wrap: wrap; gap: 20px 36px; margin-bottom: 18px;
    font-size: 13px; }}
  .meta div span {{ display: block; color: var(--muted); font-size: 11px;
    text-transform: uppercase; letter-spacing: .04em; }}
  table {{ width: 100%; border-collapse: collapse; background: #fff;
    border: 1px solid var(--line); border-radius: 10px; overflow: hidden; }}
  td {{ padding: 14px 16px; border-top: 1px solid var(--line); vertical-align: top; }}
  tr:first-child td {{ border-top: none; }}
  td.sev {{ width: 90px; }}
  .rid {{ font-weight: 700; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 13px; }}
  .rdesc {{ color: var(--muted); font-size: 13px; margin-top: 2px; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 999px;
    font-size: 11px; font-weight: 700; letter-spacing: .03em; }}
  .badge.pass {{ background: var(--pass-bg); color: var(--pass); }}
  .badge.warn {{ background: var(--warn-bg); color: var(--warn); }}
  .badge.breach {{ background: var(--breach-bg); color: var(--breach); }}
  ul.findings {{ margin: 10px 0 0; padding: 0; list-style: none; }}
  ul.findings li {{ font-size: 13px; padding: 6px 0 6px 0; display: flex;
    gap: 8px; align-items: baseline; }}
  p.summary {{ color: var(--muted); font-size: 12px; margin: 8px 0 0; font-style: italic; }}
  section.changes {{ margin-top: 28px; }}
  section.changes h2 {{ font-size: 15px; margin: 0 0 2px; }}
  p.baseline {{ color: var(--muted); font-size: 12px; margin: 0 0 12px; }}
  ul.change-list {{ list-style: none; margin: 0; padding: 0; background: #fff;
    border: 1px solid var(--line); border-radius: 10px; overflow: hidden; }}
  ul.change-list > li {{ padding: 12px 16px; border-top: 1px solid var(--line);
    display: flex; flex-wrap: wrap; gap: 10px; align-items: center; font-size: 13px; }}
  ul.change-list > li:first-child {{ border-top: none; }}
  .chg-rid {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-weight: 700; }}
  .chg-sev {{ color: var(--muted); font-size: 12px; }}
  ul.subjects {{ flex-basis: 100%; margin: 4px 0 0 4px; padding: 0; list-style: none;
    font-size: 12px; }}
  ul.subjects li {{ padding: 2px 0; }}
  .subj-new {{ color: var(--breach); }}
  .subj-clear {{ color: var(--pass); }}
  footer {{ margin-top: 20px; color: var(--muted); font-size: 12px; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>Guideline Compliance Report</h1>
  <div class="banner {overall_cls}">{status}</div>
  <div class="meta">
    <div><span>Portfolio</span>{portfolio}</div>
    <div><span>Market value</span>{market_value}</div>
    <div><span>Positions</span>{positions}</div>
    <div><span>Breaches</span>{breaches}</div>
    <div><span>Warnings</span>{warnings}</div>
    <div><span>Rules</span>{rules}</div>
    {as_of}
  </div>
  <table>
    {rows}
  </table>
  {comparison}
  <footer>Generated {generated} · Guideline &amp; Compliance Monitoring Engine</footer>
</div>
</body>
</html>
"""


RENDERERS = {
    "text": render_text,
    "json": render_json,
    "html": render_html,
}

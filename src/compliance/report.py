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

if TYPE_CHECKING:  # avoid circular imports; these modules import this one.
    from compliance.batch import BatchResult
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
            Severity.ACKNOWLEDGED: "COMPLIANT (WITH EXCEPTIONS)",
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

    def acknowledged_count(self) -> int:
        """Count of ACKNOWLEDGED (waived) findings across all rules."""
        return self.counts()[Severity.ACKNOWLEDGED.name]

    def breached_rule_count(self) -> int:
        return sum(1 for r in self.results if r.severity == Severity.BREACH)

    def to_dict(self) -> dict[str, Any]:
        counts = self.counts()  # one walk over the findings, reused below
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
                "breaches": counts[Severity.BREACH.name],
                "warnings": counts[Severity.WARN.name],
                "acknowledged": counts[Severity.ACKNOWLEDGED.name],
                "finding_counts": counts,
            },
            "results": [r.to_dict() for r in self.results],
        }


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #

_ANSI = {
    Severity.PASS: "\033[32m",          # green
    Severity.INFO: "\033[36m",          # cyan
    Severity.ACKNOWLEDGED: "\033[34m",  # blue
    Severity.WARN: "\033[33m",          # yellow
    Severity.BREACH: "\033[31m",        # red
}
_BOLD = "\033[1m"
_DIM = "\033[2m"
_RESET = "\033[0m"

_BADGE = {
    Severity.PASS: "PASS",
    Severity.INFO: "INFO",
    Severity.ACKNOWLEDGED: "WAIVED",
    Severity.WARN: "WARN",
    Severity.BREACH: "BREACH",
}

# CSS class per severity, shared by the HTML renderer.
_CSS_CLASS = {
    Severity.PASS: "pass",
    Severity.INFO: "pass",
    Severity.ACKNOWLEDGED: "ack",
    Severity.WARN: "warn",
    Severity.BREACH: "breach",
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
    comparison: ReportComparison | None = None,
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
    ack = report.acknowledged_count()
    ack_note = f", {ack} acknowledged" if ack else ""
    lines.append(
        f"  Summary   : {report.breach_count()} breach(es), "
        f"{report.warn_count()} warning(s){ack_note} across {len(report.results)} rule(s)"
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
                text = f"         {summary}"
                lines.append(paint(text, _DIM) if use_color else text)
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
    comparison: ReportComparison | None = None,
) -> str:
    """Render the report as a JSON document."""
    payload = report.to_dict()
    if comparison is not None:
        payload["comparison"] = comparison.to_dict()
    return json.dumps(payload, indent=indent)


def render_html(
    report: ComplianceReport,
    *,
    comparison: ReportComparison | None = None,
) -> str:
    """Render a standalone, styled HTML report."""
    css_class = _CSS_CLASS
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
        acknowledged=report.acknowledged_count(),
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
    --ack: #0550ae; --ack-bg: #ddf4ff;
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
  .banner.ack {{ background: var(--ack-bg); color: var(--ack); }}
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
  .badge.ack {{ background: var(--ack-bg); color: var(--ack); }}
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
    <div><span>Acknowledged</span>{acknowledged}</div>
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


# --------------------------------------------------------------------------- #
# Batch rendering
# --------------------------------------------------------------------------- #

def _truncate(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 3] + "..."


def render_batch_text(
    batch: BatchResult,
    *,
    color: bool | None = None,
    stream: TextIO | None = None,
) -> str:
    """Render a one-row-per-account batch summary table."""
    stream = stream or sys.stdout
    use_color = _use_color(stream, color)

    def paint(text: str, code: str) -> str:
        return f"{code}{text}{_RESET}" if use_color else text

    width = 90
    lines = ["=" * width, paint("  BATCH COMPLIANCE SUMMARY", _BOLD), "=" * width]
    lines.append(f"  Generated : {batch.generated_at}")
    lines.append(
        f"  Accounts  : {len(batch.results)}   "
        f"Non-compliant: {batch.non_compliant_count()}   Errors: {batch.error_count()}"
    )
    lines.append("-" * width)
    lines.append(
        paint(f"  {'ACCOUNT':<34}{'STATUS':<30}{'BR':>4}{'WN':>4}{'ACK':>6}", _BOLD)
    )
    for r in batch.results:
        name = _truncate(r.name, 32)
        if r.report is None:
            status = _truncate("ERROR: " + (r.error or ""), 28)
            row = f"  {name:<34}{status:<30}{'-':>4}{'-':>4}{'-':>6}"
        else:
            rep = r.report
            row = (
                f"  {name:<34}{_truncate(rep.status_label, 28):<30}"
                f"{rep.breach_count():>4}{rep.warn_count():>4}{rep.acknowledged_count():>6}"
            )
        lines.append(paint(row, _ANSI[r.severity]))
    lines.append("=" * width)
    verdict = "ALL COMPLIANT" if batch.worst_severity() < Severity.BREACH else "ACTION REQUIRED"
    lines.append(paint(f"  RESULT: {verdict}", _BOLD + _ANSI[batch.worst_severity()]))
    lines.append("=" * width)
    return "\n".join(lines)


def render_batch_json(batch: BatchResult, *, indent: int = 2) -> str:
    return json.dumps(batch.to_dict(), indent=indent)


def render_batch_html(batch: BatchResult) -> str:
    """Render a compact standalone batch dashboard."""
    rows = []
    for r in batch.results:
        if r.report is None:
            rows.append(
                f"<tr class='breach'><td>{escape(r.name)}</td>"
                f"<td><span class='badge breach'>ERROR</span></td>"
                f"<td colspan='3' class='err'>{escape(r.error or '')}</td></tr>"
            )
            continue
        rep = r.report
        cls = _CSS_CLASS[rep.overall_severity]
        rows.append(
            f"<tr class='{cls}'><td>{escape(r.name)}</td>"
            f"<td><span class='badge {cls}'>{escape(rep.status_label)}</span></td>"
            f"<td class='num'>{rep.breach_count()}</td>"
            f"<td class='num'>{rep.warn_count()}</td>"
            f"<td class='num'>{rep.acknowledged_count()}</td></tr>"
        )
    return _BATCH_TEMPLATE.format(
        generated=escape(batch.generated_at),
        accounts=len(batch.results),
        non_compliant=batch.non_compliant_count(),
        errors=batch.error_count(),
        rows="\n".join(rows),
    )


_BATCH_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Batch Compliance Summary</title>
<style>
  :root {{
    --pass: #1a7f37; --pass-bg: #e6f4ea; --warn: #9a6700; --warn-bg: #fff4d6;
    --breach: #cf222e; --breach-bg: #ffe9e9; --ack: #0550ae; --ack-bg: #ddf4ff;
    --ink: #1f2328; --muted: #656d76; --line: #d0d7de;
  }}
  body {{ font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    color: var(--ink); margin: 0; background: #f6f8fa; }}
  .wrap {{ max-width: 820px; margin: 0 auto; padding: 32px 20px 64px; }}
  h1 {{ font-size: 20px; margin: 0 0 6px; }}
  .meta {{ color: var(--muted); font-size: 13px; margin-bottom: 18px; }}
  table {{ width: 100%; border-collapse: collapse; background: #fff;
    border: 1px solid var(--line); border-radius: 10px; overflow: hidden; }}
  th, td {{ padding: 12px 14px; border-top: 1px solid var(--line); text-align: left;
    font-size: 13px; }}
  th {{ background: #f6f8fa; font-size: 11px; text-transform: uppercase;
    letter-spacing: .04em; color: var(--muted); }}
  td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  td.err {{ color: var(--breach); }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 999px;
    font-size: 11px; font-weight: 700; }}
  .badge.pass {{ background: var(--pass-bg); color: var(--pass); }}
  .badge.warn {{ background: var(--warn-bg); color: var(--warn); }}
  .badge.breach {{ background: var(--breach-bg); color: var(--breach); }}
  .badge.ack {{ background: var(--ack-bg); color: var(--ack); }}
</style>
</head>
<body>
<div class="wrap">
  <h1>Batch Compliance Summary</h1>
  <div class="meta">{accounts} accounts · {non_compliant} non-compliant · {errors} errors
    · generated {generated}</div>
  <table>
    <tr><th>Account</th><th>Status</th><th>Breaches</th><th>Warnings</th><th>Acknowledged</th></tr>
    {rows}
  </table>
</div>
</body>
</html>
"""

BATCH_RENDERERS = {
    "text": render_batch_text,
    "json": render_batch_json,
    "html": render_batch_html,
}

"""Command-line interface for the compliance engine.

Usage
-----
    compliance-check check --portfolio PORTFOLIO --guidelines GUIDELINES \
        [--format {text,json,html}] [--output PATH] [--fail-on {breach,warn,never}]

    compliance-check list-rules

Exit codes make the tool usable as a CI/pre-trade gate:
    0  compliant (subject to --fail-on)
    1  guideline breach (or warning, if --fail-on warn)
    2  usage or input error
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from compliance import __version__
from compliance.engine import ComplianceEngine
from compliance.loaders import LoaderError, load_guidelines, load_portfolio
from compliance.models import Severity
from compliance.report import RENDERERS, render_html, render_json, render_text
from compliance.rules.base import available_rule_types

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_ERROR = 2

_FAIL_ON = {
    "breach": Severity.BREACH,
    "warn": Severity.WARN,
    "never": None,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="compliance-check",
        description="Check a portfolio against IMA-style investment guidelines.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="Evaluate a portfolio and print a report.")
    check.add_argument("--portfolio", "-p", required=True, help="Portfolio file (.csv or .json).")
    check.add_argument(
        "--guidelines", "-g", required=True, help="Guideline document (.yaml, .yml or .json)."
    )
    check.add_argument(
        "--format", "-f", choices=sorted(RENDERERS), default="text", help="Output format."
    )
    check.add_argument("--output", "-o", help="Write the report to a file instead of stdout.")
    check.add_argument("--name", help="Override the portfolio name.")
    check.add_argument("--as-of", help="Override / set the as-of date (informational).")
    check.add_argument(
        "--fail-on",
        choices=sorted(_FAIL_ON),
        default="breach",
        help="Severity that yields a non-zero exit code (default: breach).",
    )
    check.add_argument("--no-color", action="store_true", help="Disable ANSI colour in text output.")
    check.set_defaults(func=_cmd_check)

    listing = sub.add_parser("list-rules", help="List registered rule types.")
    listing.set_defaults(func=_cmd_list_rules)

    return parser


def _cmd_list_rules(_: argparse.Namespace) -> int:
    print("Available rule types:")
    for rule_type in available_rule_types():
        print(f"  - {rule_type}")
    return EXIT_OK


def _cmd_check(args: argparse.Namespace) -> int:
    try:
        portfolio = load_portfolio(args.portfolio, name=args.name, as_of=args.as_of)
        guidelines = load_guidelines(args.guidelines)
        engine = ComplianceEngine.from_config(guidelines)
    except (LoaderError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    report = engine.run(portfolio)

    if args.format == "text":
        # Never emit ANSI colour into a file; auto-detect only for a live stdout.
        color = False if (args.no_color or args.output) else None
        rendered = render_text(report, color=color)
    elif args.format == "json":
        rendered = render_json(report)
    else:
        rendered = render_html(report)

    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
        print(f"Report written to {args.output}", file=sys.stderr)
    else:
        print(rendered)

    threshold = _FAIL_ON[args.fail_on]
    if threshold is not None and report.overall_severity >= threshold:
        return EXIT_FINDINGS
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

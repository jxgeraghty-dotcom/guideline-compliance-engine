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
from compliance.batch import evaluate_account, run_manifest
from compliance.loaders import LoaderError, load_manifest
from compliance.models import FxError, Severity
from compliance.report import (
    RENDERERS,
    render_batch_html,
    render_batch_json,
    render_batch_text,
    render_html,
    render_json,
    render_text,
)
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
    check.add_argument("--base-currency", help="Override the portfolio base currency.")
    check.add_argument(
        "--baseline",
        help="A prior report JSON to compare against (adds a 'changes since' section).",
    )
    check.add_argument(
        "--fail-on",
        choices=sorted(_FAIL_ON),
        default="breach",
        help="Severity that yields a non-zero exit code (default: breach).",
    )
    check.add_argument("--no-color", action="store_true", help="Disable ANSI colour in text output.")
    check.set_defaults(func=_cmd_check)

    batch = sub.add_parser(
        "check-batch", help="Evaluate a book of accounts from a manifest."
    )
    batch.add_argument(
        "--manifest", "-m", required=True, help="Manifest (.yaml, .yml or .json)."
    )
    batch.add_argument(
        "--format", "-f", choices=sorted(RENDERERS), default="text", help="Output format."
    )
    batch.add_argument("--output", "-o", help="Write the summary to a file instead of stdout.")
    batch.add_argument(
        "--fail-on",
        choices=sorted(_FAIL_ON),
        default="breach",
        help="Severity (worst across accounts) that yields a non-zero exit code.",
    )
    batch.add_argument("--no-color", action="store_true", help="Disable ANSI colour.")
    batch.set_defaults(func=_cmd_check_batch)

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
        report, comparison = evaluate_account(
            args.portfolio,
            args.guidelines,
            baseline_path=args.baseline,
            name=args.name,
            base_currency=args.base_currency,
            as_of=args.as_of,
        )
    except (LoaderError, ValueError, FxError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    if args.format == "text":
        # Never emit ANSI colour into a file; auto-detect only for a live stdout.
        color = False if (args.no_color or args.output) else None
        rendered = render_text(report, color=color, comparison=comparison)
    elif args.format == "json":
        rendered = render_json(report, comparison=comparison)
    else:
        rendered = render_html(report, comparison=comparison)

    _emit(rendered, args.output)

    threshold = _FAIL_ON[args.fail_on]
    if threshold is not None and report.overall_severity >= threshold:
        return EXIT_FINDINGS
    return EXIT_OK


def _cmd_check_batch(args: argparse.Namespace) -> int:
    try:
        manifest = load_manifest(args.manifest)
    except (LoaderError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    base_dir = Path(args.manifest).resolve().parent
    batch = run_manifest(manifest, base_dir)

    if args.format == "text":
        color = False if (args.no_color or args.output) else None
        rendered = render_batch_text(batch, color=color)
    elif args.format == "json":
        rendered = render_batch_json(batch)
    else:
        rendered = render_batch_html(batch)

    _emit(rendered, args.output)

    threshold = _FAIL_ON[args.fail_on]
    if threshold is not None and batch.worst_severity() >= threshold:
        return EXIT_FINDINGS
    return EXIT_OK


def _emit(rendered: str, output: str | None) -> None:
    if output:
        Path(output).write_text(rendered + "\n", encoding="utf-8")
        print(f"Report written to {output}", file=sys.stderr)
    else:
        _safe_print(rendered)


def _safe_print(text: str) -> None:
    """Print, tolerating consoles that cannot encode exotic characters.

    A limited console encoding (e.g. Windows cp1252) should never crash the run;
    fall back to replacing any unencodable characters from user-supplied data.
    """
    try:
        print(text)
    except UnicodeEncodeError:
        stream = sys.stdout
        encoding = getattr(stream, "encoding", None) or "utf-8"
        buffer = getattr(stream, "buffer", None)
        if buffer is not None:
            buffer.write((text + "\n").encode(encoding, errors="replace"))
        else:  # pragma: no cover - stream without a byte buffer
            print(text.encode(encoding, errors="replace").decode(encoding))


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

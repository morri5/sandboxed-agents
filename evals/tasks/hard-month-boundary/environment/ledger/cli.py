"""The `ledger` command."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from ledger import config, exporters, reports, validation
from ledger.parsers import ParseError, load_invoices, load_payments
from ledger.reconcile import reconcile
from ledger.timeutil import TimeError, parse_month, zone


def _report(args: argparse.Namespace) -> int:
    year, month = parse_month(args.month)
    invoices = load_invoices(args.file)
    report = reports.monthly_report(invoices, year, month, zone(args.tz), args.currency)
    print(reports.render(report))
    return 0


def _summary(args: argparse.Namespace) -> int:
    invoices = load_invoices(args.file)
    totals = reports.yearly_summary(invoices, args.year, zone(args.tz), args.currency)
    print(reports.render_summary(args.year, args.currency, totals))
    return 0


def _export(args: argparse.Namespace) -> int:
    invoices = load_invoices(args.file)
    if args.customer:
        invoices = [i for i in invoices if i.customer_id == args.customer]
    sys.stdout.write(exporters.get_exporter(args.format)(invoices))
    return 0


def _reconcile(args: argparse.Namespace) -> int:
    result = reconcile(load_invoices(args.invoices), load_payments(args.payments))
    for match in result.matches:
        print(f"paid     {match.invoice}  <- {match.payment}")
    for payment in result.unmatched_payments:
        print(f"unknown  {payment.reference}  {payment.amount} ({payment.customer_id})")
    for invoice in result.open_invoices:
        print(f"open     {invoice.number}")
    return 0


def _check(args: argparse.Namespace) -> int:
    invoices = load_invoices(args.file)
    found = [p for invoice in invoices for p in validation.problems(invoice)]
    for problem in found:
        print(problem)
    print(f"{len(invoices)} invoices, {len(found)} problems")
    return 1 if found else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ledger", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    report = commands.add_parser("report", help="monthly report")
    report.add_argument("file", type=Path)
    report.add_argument("--month", required=True, help="YYYY-MM")
    report.add_argument("--tz", default=config.DEFAULT_TIMEZONE, help="IANA time zone")
    report.add_argument("--currency", default=config.DEFAULT_CURRENCY)
    report.set_defaults(func=_report)

    summary = commands.add_parser("summary", help="totals per month for a year")
    summary.add_argument("file", type=Path)
    summary.add_argument("--year", type=int, required=True)
    summary.add_argument("--tz", default=config.DEFAULT_TIMEZONE, help="IANA time zone")
    summary.add_argument("--currency", default=config.DEFAULT_CURRENCY)
    summary.set_defaults(func=_summary)

    export = commands.add_parser("export", help="export invoices")
    export.add_argument("file", type=Path)
    export.add_argument(
        "--format",
        default="text",
        choices=exporters.available_formats(),
        help="output format: %(choices)s",
    )
    export.add_argument("--customer", help="only this customer's invoices")
    export.set_defaults(func=_export)

    rec = commands.add_parser("reconcile", help="pair payments with invoices")
    rec.add_argument("invoices", type=Path)
    rec.add_argument("payments", type=Path)
    rec.set_defaults(func=_reconcile)

    check = commands.add_parser("check", help="validate an invoice file")
    check.add_argument("file", type=Path)
    check.set_defaults(func=_check)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        code: int = args.func(args)
    except (ParseError, TimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return code

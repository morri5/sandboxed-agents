import contextlib
import io
import tempfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from ledger.cli import main
from ledger.models import Invoice, Line
from ledger.reports import invoices_in_month, monthly_report, yearly_summary

BERLIN = ZoneInfo("Europe/Berlin")
NEW_YORK = ZoneInfo("America/New_York")


def invoice(number: str, issued: datetime) -> Invoice:
    return Invoice(number, "C-1", issued, "EUR", (Line("Work", Decimal("1"), Decimal("10.00"), "zero"),))


def numbers(invoices: list[Invoice]) -> list[str]:
    return [i.number for i in invoices]


def test_april_first_is_not_in_march() -> None:
    invoices = [
        invoice("last-march", datetime(2026, 3, 31, 23, 59, tzinfo=BERLIN)),
        invoice("first-april", datetime(2026, 4, 1, 0, 20, tzinfo=BERLIN)),
        invoice("april-late-night", datetime(2026, 4, 1, 0, 59, tzinfo=BERLIN)),
    ]
    assert numbers(invoices_in_month(invoices, 2026, 3, BERLIN)) == ["last-march"]
    assert numbers(invoices_in_month(invoices, 2026, 4, BERLIN)) == ["first-april", "april-late-night"]


def test_same_instant_with_another_offset() -> None:
    issued = datetime(2026, 4, 1, 0, 20, tzinfo=BERLIN).astimezone(UTC)  # 2026-03-31T22:20Z
    invoices = [invoice("first-april", issued)]
    assert numbers(invoices_in_month(invoices, 2026, 3, BERLIN)) == []
    assert numbers(invoices_in_month(invoices, 2026, 4, BERLIN)) == ["first-april"]


def test_last_evening_of_october_is_in_october() -> None:
    late = invoice("halloween", datetime(2026, 10, 31, 23, 30, tzinfo=BERLIN))
    assert numbers(invoices_in_month([late], 2026, 10, BERLIN)) == ["halloween"]
    assert numbers(invoices_in_month([late], 2026, 11, BERLIN)) == []


def test_new_york() -> None:
    invoices = [
        invoice("march", datetime(2026, 3, 31, 23, 30, tzinfo=NEW_YORK)),
        invoice("april", datetime(2026, 4, 1, 0, 30, tzinfo=NEW_YORK)),
    ]
    assert numbers(invoices_in_month(invoices, 2026, 3, NEW_YORK)) == ["march"]
    assert numbers(invoices_in_month(invoices, 2026, 4, NEW_YORK)) == ["april"]


def test_every_hour_of_the_year_is_in_exactly_its_own_month() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC) - timedelta(hours=14)
    instants = [start + timedelta(minutes=30 + 60 * h) for h in range(366 * 24)]
    for name in ("Europe/Berlin", "America/New_York", "Australia/Sydney", "Asia/Kolkata", "UTC"):
        tz = ZoneInfo(name)
        invoices = [invoice(str(n), t) for n, t in enumerate(instants)]
        seen: dict[str, tuple[int, int]] = {}
        for year, month in [(2025, 12)] + [(2026, m) for m in range(1, 13)]:
            for i in invoices_in_month(invoices, year, month, tz):
                assert i.number not in seen, (name, i.issued_at, seen[i.number], (year, month))
                seen[i.number] = (year, month)
        for i in invoices:
            local = i.issued_at.astimezone(tz)
            if local.year == 2026 or (local.year, local.month) == (2025, 12):
                assert seen.get(i.number) == (local.year, local.month), (name, i.issued_at)


def test_report_totals_follow_the_month() -> None:
    invoices = [
        invoice("last-march", datetime(2026, 3, 31, 23, 59, tzinfo=BERLIN)),
        invoice("first-april", datetime(2026, 4, 1, 0, 20, tzinfo=BERLIN)),
    ]
    assert monthly_report(invoices, 2026, 3, BERLIN).total == Decimal("10.00")
    assert monthly_report(invoices, 2026, 4, BERLIN).total == Decimal("10.00")


def test_winter_months_are_unchanged() -> None:
    invoices = [
        invoice("new-year", datetime(2026, 1, 1, 0, 10, tzinfo=BERLIN)),
        invoice("old-year", datetime(2025, 12, 31, 23, 50, tzinfo=BERLIN)),
    ]
    assert numbers(invoices_in_month(invoices, 2026, 1, BERLIN)) == ["new-year"]
    assert numbers(invoices_in_month(invoices, 2025, 12, BERLIN)) == ["old-year"]


CSV = """number,customer_id,issued_at,currency,description,quantity,unit_price,tax_code
INV-2026-0331,C-307,2026-03-31T23:10:00+02:00,EUR,Hosting March,1,35.00,standard
INV-2026-0412,C-221,2026-04-01T00:20:00+02:00,EUR,Hosting April,1,35.00,standard
"""


def test_cli_report() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "invoices.csv"
        path.write_text(CSV)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            assert main(["report", str(path), "--month", "2026-03", "--tz", "Europe/Berlin"]) == 0
    assert "INV-2026-0331" in out.getvalue()
    assert "INV-2026-0412" not in out.getvalue()


def test_yearly_summary_puts_april_first_in_april() -> None:
    invoices = [
        invoice("last-march", datetime(2026, 3, 31, 23, 59, tzinfo=BERLIN)),
        invoice("first-april", datetime(2026, 4, 1, 0, 20, tzinfo=BERLIN)),
    ]
    totals = yearly_summary(invoices, 2026, BERLIN)
    assert totals[3] == Decimal("10.00")
    assert totals[4] == Decimal("10.00")


def test_yearly_summary_matches_the_monthly_reports() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC) - timedelta(hours=14)
    invoices = [invoice(str(h), start + timedelta(minutes=30 + 60 * h)) for h in range(0, 366 * 24, 3)]
    for name in ("Europe/Berlin", "America/New_York", "Australia/Sydney"):
        tz = ZoneInfo(name)
        totals = yearly_summary(invoices, 2026, tz)
        assert sorted(totals) == list(range(1, 13))
        for month in range(1, 13):
            assert totals[month] == monthly_report(invoices, 2026, month, tz).total, (name, month)


def test_cli_summary() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "invoices.csv"
        path.write_text(CSV)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            assert main(["summary", str(path), "--year", "2026", "--tz", "Europe/Berlin"]) == 0
    lines = {line.split()[0]: line.split()[1] for line in out.getvalue().splitlines() if line.startswith("2026-")}
    assert lines["2026-03"] == "41.65"
    assert lines["2026-04"] == "41.65"

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from ledger.cli import main

INVOICES = """number,customer_id,issued_at,currency,description,quantity,unit_price,tax_code
INV-2026-0001,C-1,2026-03-02T09:15:00+01:00,EUR,Consulting,2,120.00,standard
INV-2026-0002,C-2,2026-03-03T10:00:00+01:00,EUR,Workshop,1,900.00,standard
INV-2026-0003,C-1,2026-04-07T10:00:00+02:00,EUR,Hosting,1,35.00,standard
"""


def run(*args: str) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = main(list(args))
    return code, out.getvalue()


class CliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.file = Path(self.dir.name) / "invoices.csv"
        self.file.write_text(INVOICES)

    def tearDown(self) -> None:
        self.dir.cleanup()

    def test_report(self) -> None:
        code, out = run("report", str(self.file), "--month", "2026-03", "--tz", "Europe/Berlin")
        self.assertEqual(code, 0)
        self.assertIn("INV-2026-0002", out)
        self.assertNotIn("INV-2026-0003", out)

    def test_export_one_customer(self) -> None:
        code, out = run("export", str(self.file), "--format", "csv", "--customer", "C-1")
        self.assertEqual(code, 0)
        self.assertEqual([line.split(",")[0] for line in out.splitlines()[1:]], ["INV-2026-0001", "INV-2026-0003"])

    def test_export_to_a_file(self) -> None:
        target = Path(self.dir.name) / "out.json"
        code, out = run("export", str(self.file), "--output", str(target))
        self.assertEqual((code, out), (0, ""))
        self.assertIn('"INV-2026-0003"', target.read_text())

    def test_check(self) -> None:
        code, out = run("check", str(self.file))
        self.assertEqual(code, 0)
        self.assertIn("3 invoices, 0 problems", out)


if __name__ == "__main__":
    unittest.main()

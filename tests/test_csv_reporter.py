"""Pruebas de las puertas finales de formato del reporter CSV."""

from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from app.models.schemas import FieldResult, PageResult, Status, ValidationReport
from app.reports.csv_reporter import CsvReporter


def _field(fid: str, value, conf=0.9) -> FieldResult:
    return FieldResult(page_number=1, field_id=fid, field_type="ocr",
                       value=value, confidence=conf)


def _report(*pages: PageResult) -> ValidationReport:
    return ValidationReport(pdf_path="fixture.pdf", template_name="fixture",
                            pages=list(pages))


class TestCsvGates(unittest.TestCase):
    def _rows(self, page: PageResult) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.csv"
            CsvReporter().write(_report(page), path)
            with open(path, encoding="utf-8-sig", newline="") as fh:
                return list(csv.DictReader(fh))[0]

    def test_matricula_gate(self):
        page = PageResult(page_number=1)
        page.add_field(_field("matricula", "All H89916cmp FL /C"))
        row = self._rows(page)
        self.assertEqual(row["matricula"], "")

        page = PageResult(page_number=1)
        page.add_field(_field("matricula", "HP-1534CMP"))
        row = self._rows(page)
        self.assertEqual(row["matricula"], "HP-1534CMP")

    def test_date_part_gates(self):
        page = PageResult(page_number=1)
        page.add_field(_field("day", "Schedule Fit 211 DATE"))
        page.add_field(_field("month", "51012"))
        page.add_field(_field("year", "8313"))
        row = self._rows(page)
        self.assertEqual(row["day"], "")
        self.assertEqual(row["month"], "")
        self.assertEqual(row["year"], "")

    def test_three_digit_year_gated_out(self):
        for bad_year in ("723", "216"):
            page = PageResult(page_number=1)
            page.add_field(_field("year", bad_year))
            row = self._rows(page)
            self.assertEqual(row["year"], "")

    def test_date_column_from_page_date(self):
        page = PageResult(page_number=1, date="2026/07/16")
        page.add_field(_field("day", "16"))
        page.add_field(_field("month", "JUL"))
        page.add_field(_field("year", "26"))
        row = self._rows(page)
        self.assertEqual(row["date"], "2026/07/16")
        self.assertEqual(row["year"], "26")

    def test_canonical_values_pass(self):
        page = PageResult(page_number=1)
        page.add_field(_field("day", "23"))
        page.add_field(_field("month", "JUL"))
        page.add_field(_field("year", "2026"))
        row = self._rows(page)
        self.assertEqual(row["day"], "23")
        self.assertEqual(row["month"], "JUL")
        self.assertEqual(row["year"], "2026")


if __name__ == "__main__":
    unittest.main()

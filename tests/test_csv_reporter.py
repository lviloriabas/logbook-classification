"""Pruebas de las puertas finales de formato del reporter CSV."""

from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from app.models.schemas import FieldResult, PageResult, Status, ValidationReport
from app.reports.csv_reporter import CsvReporter
from app.templates.schema import FieldTemplate, Template


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

    def test_signature_columns_omit_status_and_comment(self):
        template = Template(
            name="fixture",
            fields=[
                FieldTemplate(id="pilot_signature", type="signature",
                              x=0.1, y=0.1, w=0.2, h=0.1),
                FieldTemplate(id="matricula", x=0.1, y=0.2, w=0.2, h=0.1),
            ],
        )
        page = PageResult(page_number=1)
        page.add_field(_field("pilot_signature", "presente", 0.8))
        page.add_field(_field("matricula", "HP-1534CMP"))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "out.csv"
            CsvReporter().write(_report(page), path, template)
            with open(path, encoding="utf-8-sig", newline="") as fh:
                row = list(csv.DictReader(fh))[0]
        self.assertNotIn("pilot_signature_status", row)
        self.assertNotIn("pilot_signature_comment", row)
        self.assertEqual(row["pilot_signature"], "presente")
        self.assertEqual(row["pilot_signature_conf"], "0.8")
        self.assertEqual(row["pilot_signature_source"], "direct")
        self.assertIn("matricula_status", row)
        self.assertIn("matricula_comment", row)


if __name__ == "__main__":
    unittest.main()

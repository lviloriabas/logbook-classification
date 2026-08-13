"""Pruebas del PDF de debug con páginas fuente sin anotaciones."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pymupdf as fitz

from app.models.schemas import FieldResult, PageResult, Status, ValidationReport
from app.reports.debug_pdf import write_debug_pdf
from app.templates.manager import TemplateManager

INPUT = Path(__file__).resolve().parents[1] / "input"


def _make_report(pdf_path: Path, field_ids) -> ValidationReport:
    """Reporte sintético con una página y campos de estados variados."""
    statuses = (Status.OK, Status.WARNING, Status.ERROR)
    fields = []
    for index, field_id in enumerate(field_ids):
        fields.append(
            FieldResult(
                page_number=1,
                field_id=field_id,
                field_type="ocr",
                value=f"VAL{index}",
                confidence=round(max(0.3, 0.9 - index * 0.1), 3),
                status=statuses[index % 3],
            )
        )
    return ValidationReport(
        pdf_path=str(pdf_path),
        template_name="Aircraft Log",
        pages=[PageResult(page_number=1, fields=fields)],
        summary={"total_pages": 1, "ok_pages": 1,
                 "warning_pages": 0, "error_pages": 0, "blank_pages": 0},
    )


class TestDebugPdf(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(__file__).resolve().parents[1]

    @unittest.skipUnless(INPUT.joinpath("test.pdf").exists(),
                         "requiere input/test.pdf")
    def test_generates_combined_pdf(self):
        template = TemplateManager().load(
            self.root / "template/aircraft_log.json"
        )
        report = _make_report(INPUT / "test.pdf",
                              [f.id for f in template.fields])
        out = Path(tempfile.mkdtemp()) / "debug.pdf"

        result = write_debug_pdf([report], template, out, dpi=120)

        self.assertEqual(result, out)
        self.assertTrue(out.exists())
        self.assertGreater(out.stat().st_size, 0)
        with fitz.open(str(INPUT / "test.pdf")) as source, \
                fitz.open(str(out)) as doc:
            self.assertEqual(len(doc), 1)
            self.assertEqual(doc[0].rect, source[0].rect)
            self.assertEqual(doc[0].get_text(), source[0].get_text())
            self.assertEqual(
                len(doc[0].get_images(full=True)),
                len(source[0].get_images(full=True)),
            )

    @unittest.skipUnless(INPUT.joinpath("test.pdf").exists(),
                         "requiere input/test.pdf")
    def test_blank_page_included(self):
        template = TemplateManager().load(
            self.root / "template/aircraft_log.json"
        )
        report = _make_report(INPUT / "test.pdf",
                              [f.id for f in template.fields])
        report.pages[0].blank = True
        report.pages[0].fields = []
        out = Path(tempfile.mkdtemp()) / "debug.pdf"

        write_debug_pdf([report], template, out, dpi=120)

        with fitz.open(str(out)) as doc:
            self.assertEqual(len(doc), 1)


if __name__ == "__main__":
    unittest.main()

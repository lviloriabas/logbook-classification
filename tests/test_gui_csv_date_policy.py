"""La política de fecha actualiza el CSV sin volver a ejecutar OCR."""

from __future__ import annotations

import csv
import os
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.gui.main_window import MainWindow
from app.models.schemas import FieldResult, PageResult, ValidationReport
from app.reports.csv_reporter import CsvReporter
from app.reports.outputs import complete_csv_path
from app.templates.schema import FieldTemplate, Template


def _field(field_id: str, value: str) -> FieldResult:
    return FieldResult(
        page_number=1,
        field_id=field_id,
        field_type="ocr",
        value=value,
        confidence=0.9,
    )


def _read_row(path: Path) -> dict[str, str]:
    with open(path, encoding="utf-8-sig", newline="") as handle:
        return next(csv.DictReader(handle))


def test_combo_rewrites_only_csv_between_specific_day_and_month_end():
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        template = Template(
            name="fecha",
            fields=[
                FieldTemplate(id="day", x=0.1, y=0.1, w=0.1, h=0.1),
                FieldTemplate(id="month", x=0.2, y=0.1, w=0.1, h=0.1),
                FieldTemplate(id="year", x=0.3, y=0.1, w=0.1, h=0.1),
            ],
        )
        page = PageResult(page_number=1, date="2026/07/14")
        page.fields = [
            _field("day", "14"),
            _field("month", "JUL"),
            _field("year", "26"),
        ]
        reports = [ValidationReport(
            pdf_path="fixture.pdf",
            template_name="fecha",
            pages=[page],
        )]

        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "BITS 14 AUG 2026 12 00"
            csv_path = run / "datos" / f"{run.name}.CSV"
            CsvReporter().write(reports, csv_path, template)
            window._reports = reports
            window._processed_template = template
            window._corrida_dir = run

            # La lista abre en «Fin de mes»; se toca la otra y se vuelve.
            window.csv_date_mode_combo.setCurrentIndex(1)
            specific_minimal = _read_row(csv_path)
            specific = _read_row(complete_csv_path(csv_path))
            assert specific_minimal["date"] == "2026/07/14"
            assert specific["day"] == "14"
            assert specific["date"] == "2026/07/14"

            window.csv_date_mode_combo.setCurrentIndex(0)
            month_end_minimal = _read_row(csv_path)
            month_end = _read_row(complete_csv_path(csv_path))
            assert month_end_minimal["date"] == "2026/07/31"
            assert month_end["day"] == "31"
            assert month_end["date"] == "2026/07/31"
            assert month_end["day_source"] == "csv_date_policy"

            # La política de salida es reversible: no muta el OCR en memoria.
            assert page.date == "2026/07/14"
            assert page.fields[0].value == "14"
    finally:
        window.close()
        app.processEvents()

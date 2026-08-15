"""Presentacion compacta de duplicados en la tabla principal."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from app.gui.main_window import MainWindow, _COLORS
from app.models.schemas import FieldResult, PageResult, Status, ValidationReport
from app.reports.csv_reporter import CsvReporter
from app.templates.schema import FieldTemplate, Template


def _page(page_number: int, log_number: str) -> PageResult:
    return PageResult(
        page_number=page_number,
        fields=[
            FieldResult(
                page_number=page_number,
                field_id="log_number",
                field_type="ocr",
                value=log_number,
            )
        ],
    )


def test_main_table_adds_colored_important_dup_from_csv_columns():
    app = QApplication.instance() or QApplication([])
    template = Template(
        name="fixture",
        fields=[
            FieldTemplate(
                id="log_number",
                x=0.1,
                y=0.1,
                w=0.2,
                h=0.1,
                required=True,
            )
        ],
    )
    reports = [
        ValidationReport(
            pdf_path="first.pdf",
            template_name="fixture",
            pages=[_page(1, "2147300")],
        ),
        ValidationReport(
            pdf_path="second.pdf",
            template_name="fixture",
            pages=[_page(7, "2147300")],
        ),
    ]
    window = MainWindow()
    try:
        window._processed_template = template
        window._populate_table(reports)
        window._table_timer.stop()
        while window._table_pending:
            window._on_table_chunk()

        duplicate_column = window._table_columns.index("dup")
        assert window.table.item(0, duplicate_column).text() == "false"
        duplicate_item = window.table.item(1, duplicate_column)
        assert duplicate_item.text() == "true"
        assert duplicate_item.background().color() == QColor(
            _COLORS[Status.WARNING]
        )
        assert "ya apareció antes" in duplicate_item.toolTip()
        assert not window.table.isColumnHidden(duplicate_column)
        assert window.duplicates_label.text() == "Duplicados: 1"
        assert "log page 00" in window.duplicates_label.toolTip()
        assert "first.pdf PDF p. 1" in window.duplicates_label.toolTip()
        assert "second.pdf PDF p. 7" in window.duplicates_label.toolTip()

        assert CsvReporter.columns_for(reports, template)[:4] == [
            "file",
            "page",
            "log_number",
            "dup",
        ]
    finally:
        window.close()
        app.processEvents()

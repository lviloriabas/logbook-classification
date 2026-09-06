"""Presentacion compacta de duplicados en la tabla principal."""

from __future__ import annotations

from PySide6.QtGui import QColor

from app.gui.main_window import _COLORS
from app.models.schemas import FieldResult, PageResult, Status, ValidationReport
from app.reports.csv_reporter import CsvReporter
from app.templates.schema import FieldTemplate, Template


def test_main_table_uses_historic_csv_indicator_colors():
    assert _COLORS == {
        Status.OK: "#1a7f37",
        Status.WARNING: "#9a6700",
        Status.ERROR: "#cf222e",
    }


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


def test_main_table_adds_colored_important_dup_from_csv_columns(window):
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
    window._processed_template = template
    window._populate_table(reports)
    window._table_timer.stop()
    while window._table_pending:
        window._on_table_chunk()

    duplicate_column = window._table_columns.index("dup")
    # Las dos filas del choque se marcan y se pintan: mirar una sola
    # obligaba a buscar a mano con cuál chocaba.
    primera = window.table.item(0, duplicate_column)
    duplicate_item = window.table.item(1, duplicate_column)
    assert primera.text() == "true"
    assert duplicate_item.text() == "true"
    for item in (primera, duplicate_item):
        assert item.background().color() == QColor(_COLORS[Status.WARNING])
    assert "es la primera de ellas" in primera.toolTip()
    assert "no es la primera" in duplicate_item.toolTip()
    assert not window.table.isColumnHidden(duplicate_column)
    assert window.duplicates_label.text() == "Duplicados: 2"
    assert "página 00 del libro" in window.duplicates_label.toolTip()
    # El detalle dice cuál de las dos sobrevive a depurar.
    assert (
        "first.pdf PDF p. 1 (se conserva)"
        in window.duplicates_label.toolTip()
    )
    assert "second.pdf PDF p. 7" in window.duplicates_label.toolTip()

    assert CsvReporter.columns_for(reports, template)[:4] == [
        "file",
        "page",
        "log_number",
        "dup",
    ]

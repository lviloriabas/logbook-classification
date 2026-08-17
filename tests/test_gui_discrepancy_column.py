"""La columna ``disc`` de la tabla principal coincide con el CSV.

La clasificación de discrepancias la ejecutaba solo la escritura de salidas,
en un hilo de fondo y después de armar la tabla: la pantalla mostraba
``false`` en páginas que el CSV marcaba como discrepancia.
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.gui.main_window import MainWindow
from app.models.schemas import FieldResult, PageResult, Status, ValidationReport
from app.templates.manager import TemplateManager

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = TemplateManager().load(ROOT / "template/aircraft_log.json")

PRESENTE = 0.9
AUSENTE = 0.85


def _page(page_number: int, log_number: str, **firmas: tuple[str, float]
          ) -> PageResult:
    page = PageResult(page_number=page_number)
    page.add_field(FieldResult(
        page_number=page_number, field_id="log_number", field_type="ocr",
        value=log_number, confidence=1.0, status=Status.OK,
    ))
    for field_id, (value, confidence) in firmas.items():
        page.add_field(FieldResult(
            page_number=page_number, field_id=field_id,
            field_type="signature", value=value, confidence=confidence,
            status=Status.OK if value == "true" else Status.ERROR,
        ))
    return page


def _vuelo(page_number: int, log_number: str, **extra) -> PageResult:
    firmas = {
        "pilot_signature": ("true", PRESENTE),
        "captain_signature": ("true", PRESENTE),
        "captain_license": ("true", PRESENTE),
        "technician_signature": ("false", AUSENTE),
        "technician_license": ("false", AUSENTE),
    }
    firmas.update(extra)
    return _page(page_number, log_number, **firmas)


def test_disc_column_shows_the_classified_pages():
    app = QApplication.instance() or QApplication([])
    reports = [ValidationReport(
        pdf_path="bitacora.pdf",
        template_name=TEMPLATE.name,
        pages=[
            _vuelo(1, "2147337"),
            _vuelo(2, "2147338", captain_signature=("false", AUSENTE)),
        ],
    )]
    window = MainWindow()
    try:
        window._processed_template = TEMPLATE
        window._populate_table(reports)
        window._table_timer.stop()
        while window._table_pending:
            window._on_table_chunk()

        column = window._table_columns.index("disc")
        assert window.table.item(0, column).text() == "false"
        marcada = window.table.item(1, column)
        assert marcada.text() == "true"
        assert "discrepancia" in marcada.toolTip()
        # Es un indicador de la bitácora: se ve también en la vista resumida.
        assert not window.table.isColumnHidden(column)
    finally:
        window.close()
        app.processEvents()

"""Contrato del flujo de fechas que la GUI entrega al pipeline."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.gui.main_window import MainWindow
from app.reports.csv_reporter import CSV_DATE_MONTH_END, CSV_DATE_SPECIFIC


def test_gui_uses_fixed_ocr_without_chained_fallbacks():
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        config = window._current_processing_config()
        assert config.date_slot_ocr is False
        assert config.date_dynamic_geometry is True
        assert window.important_fields_check.isEnabled() is False
        window.fields_check.setChecked(True)
        assert window.important_fields_check.isEnabled() is True
        assert window._export_options().debug is False
        assert window._csv_date_mode() == CSV_DATE_SPECIFIC
        assert window._export_options().csv_date_mode == CSV_DATE_SPECIFIC
        window.csv_date_mode_combo.setCurrentIndex(1)
        assert window._csv_date_mode() == CSV_DATE_MONTH_END
        assert window._export_options().csv_date_mode == CSV_DATE_MONTH_END
    finally:
        window.close()
        app.processEvents()

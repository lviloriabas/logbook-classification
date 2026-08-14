"""Contrato del flujo de fechas que la GUI entrega al pipeline."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.gui.main_window import MainWindow


def test_gui_enables_structured_dates_and_disables_vlm():
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        config = window._current_processing_config()
        assert config.date_slot_ocr is True
        assert config.date_dynamic_geometry is True
        assert config.vlm_enabled is False
        assert window.important_fields_check.isEnabled() is False
        window.fields_check.setChecked(True)
        assert window.important_fields_check.isEnabled() is True
    finally:
        window.close()
        app.processEvents()

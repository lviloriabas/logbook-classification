"""Contrato del flujo de fechas que la GUI entrega al pipeline."""

from __future__ import annotations

from app.reports.csv_reporter import CSV_DATE_MONTH_END, CSV_DATE_SPECIFIC


def test_gui_uses_fixed_ocr_without_chained_fallbacks(window):
    config = window._current_processing_config()
    assert config.date_slot_ocr is False
    assert config.date_dynamic_geometry is True
    assert window.important_fields_check.isEnabled() is False
    window.fields_check.setChecked(True)
    assert window.important_fields_check.isEnabled() is True
    assert window._export_options().debug is False
    # Con que opcion abre la ventana lo decide la ultima elegida, asi que
    # aqui se parte de una conocida en vez de darla por supuesta.
    window.csv_date_mode_combo.setCurrentIndex(0)
    assert window._csv_date_mode() == CSV_DATE_MONTH_END
    assert window._export_options().csv_date_mode == CSV_DATE_MONTH_END
    window.csv_date_mode_combo.setCurrentIndex(1)
    assert window._csv_date_mode() == CSV_DATE_SPECIFIC
    assert window._export_options().csv_date_mode == CSV_DATE_SPECIFIC

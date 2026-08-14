"""La GUI usa una configuración OCR fija y no expone motores al usuario."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QScrollArea

from app.core.config import AppConfig
from app.gui.main_window import MainWindow


def test_config_defaults_to_validated_single_engine():
    config = AppConfig()
    assert config.ocr_engine == "paddle"
    assert config.ocr_rec_model == "PP-OCRv5_mobile_rec"
    assert config.ocr_det_model == "PP-OCRv6_medium_det"
    assert config.date_ocr_fallback is False
    assert config.date_slot_ocr is False


def test_gui_has_no_engine_selectors_and_uses_validated_models():
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        assert not hasattr(window, "engine_combo")
        assert not hasattr(window, "date_engine_combo")
        assert not hasattr(window, "date_fallback_check")
        assert not hasattr(window, "date_slot_check")
        config = window._current_processing_config()
        assert config.ocr_engine == "paddle"
        assert config.date_engine_name == ""
        assert config.ocr_rec_model == "PP-OCRv5_mobile_rec"
        assert config.ocr_det_model == "PP-OCRv6_medium_det"
        assert config.date_ocr_fallback is False
        assert config.date_slot_ocr is False
    finally:
        window.close()
        app.processEvents()


def test_configuration_panel_is_not_wrapped_in_a_scroll_area():
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        assert window.findChild(QScrollArea, "controlScroll") is None
    finally:
        window.close()
        app.processEvents()

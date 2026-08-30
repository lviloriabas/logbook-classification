"""Valores fijos de procesamiento en la ventana principal."""

from __future__ import annotations

import os
from pathlib import Path

import pymupdf as fitz

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.gui.main_window import MainWindow


def _pdf(path: Path, pages: int) -> Path:
    document = fitz.open()
    for number in range(1, pages + 1):
        page = document.new_page(width=200, height=280)
        page.insert_text((40, 60), f"PAGINA {number}", fontsize=16)
    document.save(path)
    document.close()
    return path


def _window(tmp_path: Path, sizes=(10, 20, 5)) -> MainWindow:
    QApplication.instance() or QApplication([])
    window = MainWindow()
    paths = [
        _pdf(tmp_path / f"book_{index}.pdf", size)
        for index, size in enumerate(sizes)
    ]
    window._set_input_paths(paths)
    window.esperar_lectura_de_entrada()
    return window


def test_la_interfaz_procesa_el_batch_completo(tmp_path):
    window = _window(tmp_path)
    try:
        assert window._page_range().is_full
        assert window._batch_total_pages() == 35
        assert len(window._resolved_paths()) == 3
        assert not hasattr(window, "_process_group")
    finally:
        window.close()


def test_cambiar_la_entrada_mantiene_el_batch_completo(tmp_path):
    window = _window(tmp_path)
    try:
        window._set_input_paths([_pdf(tmp_path / "otra.pdf", 7)])
        window.esperar_lectura_de_entrada()
        assert window._page_range().is_full
        assert window._batch_total_pages() == 7
        assert len(window._resolved_paths()) == 1
    finally:
        window.close()


def test_las_opciones_fijas_conservan_los_valores_recomendados(tmp_path):
    window = _window(tmp_path)
    try:
        config = window._current_processing_config()
        assert config.deskew is True
        assert config.align is True
        assert config.crop_preprocess is True
        assert window._reference_page == 1
    finally:
        window.close()

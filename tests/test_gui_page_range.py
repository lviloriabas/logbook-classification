"""Control de rango de páginas de la ventana principal."""

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


def test_the_batch_starts_on_the_first_and_last_page_of_the_input(tmp_path):
    """Los dos extremos son números reales: 1 y la última del lote."""
    window = _window(tmp_path)
    try:
        assert window.page_from_spin.value() == 1
        assert window.page_to_spin.value() == 35
        assert window.page_to_spin.specialValueText() == ""
        assert window._page_range().is_full
        assert window._batch_total_pages() == 35
        assert len(window._resolved_paths()) == 3
        assert window.page_range_label.text() == "de 35 pág."
    finally:
        window.close()


def test_changing_the_input_moves_the_end_to_the_new_last_page(tmp_path):
    window = _window(tmp_path)
    try:
        assert window.page_to_spin.value() == 35
        window._set_input_paths([_pdf(tmp_path / "otra.pdf", 7)])
        window.esperar_lectura_de_entrada()
        assert window.page_from_spin.value() == 1
        assert window.page_to_spin.value() == 7
        assert window.page_range_label.text() == "de 7 pág."
    finally:
        window.close()


def test_an_empty_input_leaves_the_controls_at_one(tmp_path):
    window = _window(tmp_path)
    try:
        window._set_input_paths([])
        window.esperar_lectura_de_entrada()
        assert window.page_from_spin.value() == 1
        assert window.page_to_spin.value() == 1
        assert window.page_range_label.text() == ""
        assert window._batch_slices() == []
    finally:
        window.close()


def test_a_range_across_two_files_cuts_each_one(tmp_path):
    """8-22 del lote: las tres últimas del primero y doce del segundo."""
    window = _window(tmp_path)
    try:
        window.page_from_spin.setValue(8)
        window.page_to_spin.setValue(22)
        tramos = window._batch_slices()
        assert [(item.path.name, item.pages.first, item.pages.last)
                for item in tramos] == [
            ("book_0.pdf", 8, 10), ("book_1.pdf", 1, 12)
        ]
        assert window.page_range_label.text() == "15 de 35 pág."
    finally:
        window.close()


def test_the_last_file_alone_is_reachable_by_its_global_pages(tmp_path):
    window = _window(tmp_path)
    try:
        window.page_from_spin.setValue(31)
        assert [item.path.name for item in window._batch_slices()] == [
            "book_2.pdf"
        ]
    finally:
        window.close()


def test_the_controls_cannot_go_past_the_batch(tmp_path):
    window = _window(tmp_path)
    try:
        window.page_to_spin.setValue(999)
        assert window.page_to_spin.value() == 35
        window.page_from_spin.setValue(999)
        assert window.page_from_spin.value() == 35
    finally:
        window.close()


def test_an_end_below_the_start_drags_the_start_down(tmp_path):
    """Un rango invertido no describe nada: se corrige en vez de vaciarse."""
    window = _window(tmp_path)
    try:
        window.page_from_spin.setValue(20)
        window.page_to_spin.setValue(12)
        assert window.page_from_spin.value() == 12
        assert window._batch_slices()
    finally:
        window.close()


def test_a_start_above_the_end_drags_the_end_up(tmp_path):
    window = _window(tmp_path)
    try:
        window.page_to_spin.setValue(10)
        window.page_from_spin.setValue(25)
        assert window.page_to_spin.value() == 25
        assert [(item.path.name, item.pages.first, item.pages.last)
                for item in window._batch_slices()] == [("book_1.pdf", 15, 15)]
    finally:
        window.close()


def test_reaching_the_last_page_counts_as_the_whole_batch(tmp_path):
    """El control muestra 35, pero el rango se trata como abierto."""
    window = _window(tmp_path)
    try:
        window.page_to_spin.setValue(20)
        assert not window._page_range().is_full
        window.page_to_spin.setValue(35)
        assert window._page_range().is_full
        assert window._page_range().last is None
    finally:
        window.close()


def test_the_estimate_counts_only_the_selected_pages(tmp_path):
    window = _window(tmp_path)
    try:
        window._ms_per_page = 1000.0
        window.page_from_spin.setValue(1)
        window.page_to_spin.setValue(10)
        assert "10 páginas" in window.estimate_label.text()
        assert "1 archivos" in window.estimate_label.text()
    finally:
        window.close()


def test_the_file_rows_show_the_pages_of_the_slice(tmp_path):
    window = _window(tmp_path)
    try:
        window.page_from_spin.setValue(8)
        window.page_to_spin.setValue(22)
        window._set_file_page_counts(window._batch_slices())
        assert window._file_page_counts == [3, 12]
    finally:
        window.close()


def test_the_message_for_a_range_outside_the_batch(tmp_path):
    window = _window(tmp_path)
    try:
        window.page_from_spin.setValue(30)
        # El tope de los controles impide llegar aquí desde la interfaz; la
        # comprobación protege una entrada que cambió bajo un rango ya fijado.
        window._input_page_counts = [2, 2, 1]
        assert window._batch_slices() == []
        message = window._empty_range_message()
        assert "no incluye ninguna página" in message
        assert "5 en total" in message
    finally:
        window.close()

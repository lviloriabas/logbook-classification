"""El buscador de bitácoras de la ventana principal.

La ventana principal y el visor de CSV enseñan lo mismo: la tabla del batch
y la página de la que salió cada fila. Encontrar una bitácora tenía que ser
igual en las dos, y en la principal no había manera de buscar.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QTableWidgetItem

from app.gui.main_window import MainWindow

RAIZ = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _tabla_con(window: MainWindow, columnas: list[str], filas: list[list[str]]):
    """Deja la tabla poblada como si se hubiera procesado un batch."""
    window._table_columns = list(columnas)
    window._table_pending = []
    window.table.setColumnCount(len(columnas))
    window.table.setHorizontalHeaderLabels(columnas)
    window.table.setRowCount(len(filas))
    for indice, fila in enumerate(filas):
        for columna, valor in enumerate(fila):
            window.table.setItem(indice, columna, QTableWidgetItem(valor))


def test_buscar_una_bitacora_lleva_a_su_fila(app):
    window = MainWindow()
    try:
        _tabla_con(
            window,
            ["file", "page", "log_number"],
            [
                ["a.pdf", "1", "2147300"],
                ["a.pdf", "2", "2147301"],
                ["b.pdf", "1", "2147302"],
            ],
        )

        window.search_edit.setText("2147301")
        window._buscar_en_la_tabla()

        assert window.table.currentRow() == 1
        assert "Coincidencia 1 de 1" in window.search_context.text()
        assert "log_number" in window.search_context.text()
    finally:
        window.close()
        app.processEvents()


def test_la_bitacora_entera_gana_a_la_mencion_de_paso(app):
    """Escribir el número completo lleva a esa bitácora, no a la que lo contiene."""
    window = MainWindow()
    try:
        _tabla_con(
            window,
            ["file", "log_number"],
            [["largo.pdf", "12147300"], ["justo.pdf", "2147300"]],
        )

        window.search_edit.setText("2147300")
        window._buscar_en_la_tabla()

        assert window.table.currentRow() == 1, "la coincidencia exacta va primero"
        assert "Coincidencia 1 de 2" in window.search_context.text()
    finally:
        window.close()
        app.processEvents()


def test_repetir_la_busqueda_avanza_a_la_siguiente(app):
    window = MainWindow()
    try:
        _tabla_con(
            window,
            ["file", "matricula"],
            [["a.pdf", "YV3021"], ["b.pdf", "YV3021"], ["c.pdf", "YV1010"]],
        )

        window.search_edit.setText("YV3021")
        window._buscar_en_la_tabla()
        assert window.table.currentRow() == 0

        window._buscar_en_la_tabla()

        assert window.table.currentRow() == 1
        assert "Coincidencia 2 de 2" in window.search_context.text()
        assert window.search_next.isEnabled()
    finally:
        window.close()
        app.processEvents()


def test_sin_coincidencias_lo_dice_y_no_mueve_la_tabla(app):
    window = MainWindow()
    try:
        _tabla_con(window, ["file"], [["a.pdf"], ["b.pdf"]])
        window.table.selectRow(1)

        window.search_edit.setText("no-esta")
        window._buscar_en_la_tabla()

        assert window.table.currentRow() == 1
        assert "sin coincidencias" in window.search_context.text()
        assert not window.search_next.isEnabled()
    finally:
        window.close()
        app.processEvents()


def test_no_se_busca_en_las_columnas_que_la_vista_resumida_oculta(app):
    window = MainWindow()
    try:
        _tabla_con(
            window,
            ["file", "log_number_comment"],
            [["a.pdf", "revisar"], ["b.pdf", "otro"]],
        )
        window.table.setColumnHidden(1, True)

        window.search_edit.setText("revisar")
        window._buscar_en_la_tabla()

        assert "sin coincidencias" in window.search_context.text()
    finally:
        window.close()
        app.processEvents()


def test_rehacer_la_tabla_olvida_las_coincidencias(app):
    """Apuntaban a filas del batch anterior; seguirlas abriría otra página."""
    window = MainWindow()
    try:
        _tabla_con(window, ["file"], [["a.pdf"], ["b.pdf"]])
        window.search_edit.setText("a.pdf")
        window._buscar_en_la_tabla()
        assert window._coincidencias

        window._populate_table([])

        assert window._coincidencias == []
        assert not window.search_next.isEnabled()
    finally:
        window._table_timer.stop()
        window.close()
        app.processEvents()


def test_los_dos_visores_de_pdf_comparten_atajos(app):
    """Lo que sirve en la vista previa principal sirve en el visor de CSV."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeySequence

    from app.gui.csv_viewer import CsvViewerWindow

    window = MainWindow()
    viewer = CsvViewerWindow(RAIZ)
    try:
        assert window.btn_prev.shortcut() == QKeySequence(Qt.Key.Key_Left)
        assert viewer.pdf_viewer.prev.shortcut() == QKeySequence(Qt.Key.Key_Left)
        assert window.btn_next.shortcut() == QKeySequence(Qt.Key.Key_Right)
        assert viewer.pdf_viewer.next.shortcut() == QKeySequence(Qt.Key.Key_Right)

        def atajos(ventana) -> set[str]:
            from PySide6.QtGui import QShortcut

            return {
                atajo.key().toString()
                for atajo in ventana.findChildren(QShortcut)
                if atajo.key().toString().startswith("Ctrl+")
            }

        assert {"Ctrl++", "Ctrl+-"} <= atajos(window)
        assert {"Ctrl++", "Ctrl+-"} <= atajos(viewer)
    finally:
        viewer.pdf_viewer.shutdown()
        viewer.close()
        window.close()
        app.processEvents()

"""La fila seleccionada se pinta como una sola banda azul.

El estilo nativo de Windows 11 dibuja la selección con un rectángulo
redondeado por celda, separado del vecino: la fila se veía como una hilera de
cuadros azules sueltos. La prueba renderiza la tabla y comprueba que el azul
llega entero de un extremo al otro, sin las esquinas redondas ni los huecos de
ese estilo; lo único que puede cortarlo es la línea de rejilla de 1 px que la
tabla dibuja en todas sus filas.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSize
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QStyleFactory, QTableWidget, QTableWidgetItem

from app.gui.widgets import (
    DATA_TABLE_QSS,
    FlatSelectionDelegate,
    TABLE_SELECTION_BG,
    style_data_table,
)

_COLUMNS = 5
_ROWS = 4


def _table() -> QTableWidget:
    table = QTableWidget(_ROWS, _COLUMNS)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    table.setStyleSheet(DATA_TABLE_QSS)
    style_data_table(table)
    for row in range(_ROWS):
        for column in range(_COLUMNS):
            # Sin texto: los píxeles de las letras no son huecos de la banda.
            table.setItem(row, column, QTableWidgetItem(""))
    table.resize(QSize(500, 200))
    return table


def _selection_scanlines(image: QImage) -> dict[int, list[tuple[int, int]]]:
    """Tramos horizontales del color de selección, por línea de la imagen."""
    target = QColor(TABLE_SELECTION_BG).rgb()
    scanlines: dict[int, list[tuple[int, int]]] = {}
    for y in range(image.height()):
        runs: list[tuple[int, int]] = []
        start = None
        for x in range(image.width()):
            if image.pixel(x, y) == target:
                if start is None:
                    start = x
            elif start is not None:
                runs.append((start, x - 1))
                start = None
        if start is not None:
            runs.append((start, image.width() - 1))
        if runs:
            scanlines[y] = runs
    return scanlines


def test_selected_row_is_one_continuous_band(tmp_path):
    app = QApplication.instance() or QApplication([])
    # El estilo real de la aplicación en Windows; offscreen arranca en Fusion.
    previous = app.style().objectName()
    if "windows11" in QStyleFactory.keys():
        app.setStyle("windows11")
    table = _table()
    try:
        table.show()
        app.processEvents()
        table.selectRow(1)
        app.processEvents()

        image = QImage(table.size(), QImage.Format.Format_ARGB32)
        image.fill(0)
        table.render(image)
        scanlines = _selection_scanlines(image)

        assert scanlines, "la fila seleccionada no se pintó"
        widths = {
            sum(end - start + 1 for start, end in runs)
            for runs in scanlines.values()
        }
        # Una banda recta mide lo mismo en todas sus líneas; las esquinas
        # redondeadas del estilo nativo dejaban las primeras más cortas.
        assert len(widths) == 1
        for runs in scanlines.values():
            gaps = [
                runs[index + 1][0] - runs[index][1] - 1
                for index in range(len(runs) - 1)
            ]
            # Solo la rejilla de la tabla puede cortar el azul.
            assert all(gap == 1 for gap in gaps), gaps
    finally:
        table.close()
        app.setStyle(previous)
        app.processEvents()


def test_data_tables_paint_their_own_selection():
    app = QApplication.instance() or QApplication([])
    table = _table()
    try:
        assert isinstance(table.itemDelegate(), FlatSelectionDelegate)
    finally:
        table.close()
        app.processEvents()

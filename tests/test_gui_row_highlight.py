"""La fila entera se sombrea: bajo el cursor y cuando está marcada.

El estilo nativo de Windows resalta la celda sobre la que está el ratón, así
que recorrer una tabla dejaba un solo cuadro azul suelto en vez de la línea
que se está leyendo. Y la casilla de una fila solo pintaba su propia celda,
de modo que en una tabla ancha no había forma de ver de un vistazo qué
páginas quedaron elegidas.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QMouseEvent
from PySide6.QtWidgets import (
    QApplication,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
)

from app.gui.widgets import (
    TABLE_CHECKED_BG,
    TABLE_HOVER_BG,
    TABLE_SELECTION_BG,
    style_data_table,
)

_COLUMNAS = 5
_FILAS = 4


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _tabla() -> QTableWidget:
    tabla = QTableWidget(_FILAS, _COLUMNAS)
    tabla.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    tabla.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    for fila in range(_FILAS):
        for columna in range(_COLUMNAS):
            tabla.setItem(fila, columna, QTableWidgetItem(f"{fila}-{columna}"))
    style_data_table(tabla)
    tabla.resize(500, 200)
    return tabla


def _fondos(tabla: QTableWidget, fila: int) -> list[str]:
    """Color con el que el delegado pintaría cada celda de esa fila."""
    delegado = tabla.itemDelegate()
    colores = []
    for columna in range(_COLUMNAS):
        opcion = QStyleOptionViewItem()
        opcion.initFrom(tabla)
        delegado.initStyleOption(opcion, tabla.model().index(fila, columna))
        colores.append(opcion.backgroundBrush.color().name())
    return colores


def test_el_cursor_sombrea_la_fila_entera(app):
    tabla = _tabla()
    try:
        tabla.setProperty("hoverRow", 2)

        # Ninguna celda se queda fuera: la banda cruza la fila completa.
        assert _fondos(tabla, 2) == [QColor(TABLE_HOVER_BG).name()] * _COLUMNAS
        # Y no se derrama sobre las vecinas.
        assert QColor(TABLE_HOVER_BG).name() not in _fondos(tabla, 1)
    finally:
        tabla.close()
        app.processEvents()


def test_mover_el_raton_cambia_la_fila_resaltada(app):
    """El resalte lo lleva la vista, no cada celda por separado."""
    tabla = _tabla()
    try:
        tabla.show()
        app.processEvents()
        assert int(tabla.property("hoverRow")) == -1

        punto = tabla.visualRect(tabla.model().index(1, 0)).center()
        QApplication.sendEvent(
            tabla.viewport(),
            QMouseEvent(
                QEvent.Type.MouseMove,
                QPointF(punto),
                QPointF(tabla.viewport().mapToGlobal(punto)),
                Qt.MouseButton.NoButton,
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
            ),
        )
        assert int(tabla.property("hoverRow")) == 1

        # Al salir de la tabla no queda ninguna fila encendida.
        QApplication.sendEvent(tabla.viewport(), QEvent(QEvent.Type.Leave))
        assert int(tabla.property("hoverRow")) == -1
    finally:
        tabla.close()
        app.processEvents()


def test_la_fila_marcada_queda_sombreada_de_extremo_a_extremo(app):
    tabla = _tabla()
    try:
        # La casilla vive en su propia columna y la vista dice en cuál.
        tabla.setProperty("checkColumn", 0)
        marca = tabla.item(1, 0)
        marca.setFlags(marca.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        marca.setCheckState(Qt.CheckState.Checked)

        assert _fondos(tabla, 1) == [QColor(TABLE_CHECKED_BG).name()] * _COLUMNAS
        assert QColor(TABLE_CHECKED_BG).name() not in _fondos(tabla, 0)
    finally:
        tabla.close()
        app.processEvents()


def test_la_seleccion_manda_sobre_la_marca_y_sobre_el_cursor(app):
    """Son tres azules distintos y el de la selección es el de arriba."""
    tabla = _tabla()
    try:
        tabla.setProperty("checkColumn", 0)
        marca = tabla.item(1, 0)
        marca.setFlags(marca.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        marca.setCheckState(Qt.CheckState.Checked)
        tabla.setProperty("hoverRow", 1)
        tabla.selectRow(1)

        assert _fondos(tabla, 1) == [
            QColor(TABLE_SELECTION_BG).name()
        ] * _COLUMNAS
    finally:
        tabla.close()
        app.processEvents()


def test_una_celda_con_color_de_estado_lo_conserva_bajo_el_cursor(app):
    """El verde y el rojo son datos: el cursor los tiñe, no los borra."""
    tabla = _tabla()
    try:
        tabla.item(0, 2).setBackground(QColor("#1a7f37"))
        tabla.setProperty("hoverRow", 0)

        fondos = _fondos(tabla, 0)
        # La celda con estado no queda del gris azulado del resto…
        assert fondos[2] != QColor(TABLE_HOVER_BG).name()
        # …ni del verde que tenía: se ve que el cursor está encima.
        assert fondos[2] != QColor("#1a7f37").name()
    finally:
        tabla.close()
        app.processEvents()


def test_la_posicion_del_cursor_no_se_inventa_cuando_no_hay_ninguna(app):
    """Sin fila bajo el ratón ninguna celda se pinta de más."""
    tabla = _tabla()
    try:
        assert QColor(TABLE_HOVER_BG).name() not in _fondos(tabla, 0)
        assert tabla.property("hoverRow") is not None
    finally:
        tabla.close()
        app.processEvents()


def test_un_punto_fuera_de_las_filas_apaga_el_resalte(app):
    tabla = _tabla()
    try:
        tabla.show()
        app.processEvents()
        tabla.setProperty("hoverRow", 0)
        fuera = QPoint(10, tabla.viewport().height() - 2)

        QApplication.sendEvent(
            tabla.viewport(),
            QMouseEvent(
                QEvent.Type.MouseMove,
                QPointF(fuera),
                QPointF(tabla.viewport().mapToGlobal(fuera)),
                Qt.MouseButton.NoButton,
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
            ),
        )

        assert int(tabla.property("hoverRow")) == -1
    finally:
        tabla.close()
        app.processEvents()

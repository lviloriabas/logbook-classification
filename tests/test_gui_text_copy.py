"""Los mensajes visibles se pueden llevar al portapapeles."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
)

from app.gui.text_copy import CopyableListWidget, install_text_copy_support
from app.gui.widgets import ElidedLabel


def _app() -> QApplication:
    app = QApplication.instance() or QApplication([])
    install_text_copy_support(app)
    return app


def test_las_etiquetas_de_la_interfaz_permiten_seleccionar_texto():
    app = _app()
    label = QLabel("Error que se necesita copiar")
    label.show()
    app.processEvents()

    assert (
        label.textInteractionFlags()
        & Qt.TextInteractionFlag.TextSelectableByMouse
    )


def test_un_mensaje_recortado_copia_el_texto_completo():
    app = _app()
    message = "Error completo que no cabe en la linea de estado"
    label = ElidedLabel(message)
    label.resize(80, label.sizeHint().height())
    label.show()
    label.setFocus()
    app.processEvents()

    QTest.keyClick(label, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)

    assert QApplication.clipboard().text() == message


def test_la_bitacora_copia_varias_lineas_en_orden():
    _app()
    log = CopyableListWidget()
    log.setSelectionMode(CopyableListWidget.SelectionMode.ExtendedSelection)
    log.addItems(["10:00  Inicio", "10:01  Error", "10:02  Fin"])
    log.item(2).setSelected(True)
    log.item(1).setSelected(True)
    log.setFocus()

    QTest.keyClick(log, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)

    assert QApplication.clipboard().text() == "10:01  Error\n10:02  Fin"


def test_la_tabla_del_csv_copia_las_celdas_elegidas():
    app = _app()
    table = QTableWidget(2, 2)
    for fila, valores in enumerate((("A320", "12"), ("B737", "13"))):
        for columna, valor in enumerate(valores):
            table.setItem(fila, columna, QTableWidgetItem(valor))
    table.selectRow(0)
    table.show()
    app.processEvents()

    QApplication.clipboard().clear()
    QTest.keyClick(table, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)

    assert QApplication.clipboard().text() == "A320\t12"


def test_la_copia_omite_las_columnas_que_la_vista_resumida_oculta():
    app = _app()
    table = QTableWidget(1, 3)
    for columna, valor in enumerate(("A320", "oculta", "12")):
        table.setItem(0, columna, QTableWidgetItem(valor))
    table.setColumnHidden(1, True)
    table.selectRow(0)
    table.show()
    app.processEvents()

    QApplication.clipboard().clear()
    QTest.keyClick(table, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)

    assert QApplication.clipboard().text() == "A320\t12"


def test_una_tabla_con_menu_propio_conserva_su_menu():
    """El filtro no puede quedarse el clic derecho de la cola de AirVault."""
    app = _app()
    table = QTableWidget(1, 1)
    table.setItem(0, 0, QTableWidgetItem("batch"))
    table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    pedidos: list[object] = []
    table.customContextMenuRequested.connect(pedidos.append)
    table.show()
    app.processEvents()

    # Qt entrega el menu contextual al viewport, no a la tabla.
    viewport = table.viewport()
    event = QContextMenuEvent(
        QContextMenuEvent.Reason.Mouse,
        QPoint(5, 5),
        viewport.mapToGlobal(QPoint(5, 5)),
    )
    app.sendEvent(viewport, event)

    assert pedidos, "el menu propio de la tabla no llego a pedirse"

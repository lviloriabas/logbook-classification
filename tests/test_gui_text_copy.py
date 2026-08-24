"""Los mensajes visibles se pueden llevar al portapapeles."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel

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

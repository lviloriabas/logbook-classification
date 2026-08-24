"""Soporte comun para copiar mensajes visibles de la interfaz."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QListWidget,
    QMenu,
)


class _CopyableTextFilter(QObject):
    """Hace seleccionables las etiquetas sin volverlas editables."""

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - API Qt
        if isinstance(watched, QLabel):
            if event.type() in (QEvent.Type.Polish, QEvent.Type.Show):
                flags = watched.textInteractionFlags()
                watched.setTextInteractionFlags(
                    flags | Qt.TextInteractionFlag.TextSelectableByMouse
                )

            # ElidedLabel pinta un texto recortado, pero ``text()`` conserva
            # el mensaje completo. La copia debe llevarse ese mensaje y no
            # la version visible que termina en puntos suspensivos.
            copy_text = getattr(watched, "fullTextForCopy", None)
            if callable(copy_text):
                if (
                    event.type() == QEvent.Type.KeyPress
                    and event.matches(QKeySequence.StandardKey.Copy)
                ):
                    QApplication.clipboard().setText(copy_text())
                    event.accept()
                    return True
                if event.type() == QEvent.Type.ContextMenu:
                    menu = QMenu(watched)
                    action = menu.addAction("Copiar")
                    action.setEnabled(bool(copy_text()))
                    if menu.exec(event.globalPos()) is action:
                        QApplication.clipboard().setText(copy_text())
                    event.accept()
                    return True

        return super().eventFilter(watched, event)


def install_text_copy_support(app: QApplication | None = None) -> None:
    """Permite seleccionar y copiar el texto de las etiquetas de la GUI."""
    app = app or QApplication.instance()
    if app is None or getattr(app, "_text_copy_filter", None) is not None:
        return

    event_filter = _CopyableTextFilter(app)
    app.installEventFilter(event_filter)
    # QApplication no toma propiedad Python del filtro al instalarlo. Se
    # conserva una referencia para que no sea recolectado mientras corre Qt.
    app._text_copy_filter = event_filter

    # Se instala al arrancar, antes de crear las ventanas. Recorrer
    # ``allWidgets()`` aqui puede devolver wrappers de widgets que Qt ya
    # destruyo durante una ejecucion larga (por ejemplo, en la suite) y
    # provocar una violacion de acceso nativa. Los eventos Polish/Show cubren
    # de forma segura cada etiqueta cuando entra en uso.


class CopyableListWidget(QListWidget):
    """Lista de solo lectura cuyas lineas se pueden copiar juntas."""

    def copySelectedItems(self) -> None:  # noqa: N802 - API Qt
        items = sorted(self.selectedItems(), key=self.row)
        if items:
            QApplication.clipboard().setText(
                "\n".join(item.text() for item in items)
            )

    def keyPressEvent(self, event) -> None:  # noqa: N802 - API Qt
        if event.matches(QKeySequence.StandardKey.Copy):
            self.copySelectedItems()
            event.accept()
            return
        super().keyPressEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802 - API Qt
        item = self.itemAt(event.pos())
        if item is not None and not item.isSelected():
            self.clearSelection()
            item.setSelected(True)
            self.setCurrentItem(item)

        menu = QMenu(self)
        action = menu.addAction("Copiar")
        action.setEnabled(bool(self.selectedItems()))
        if menu.exec(event.globalPos()) is action:
            self.copySelectedItems()

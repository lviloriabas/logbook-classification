"""Selector reutilizable de columnas importantes para las dos vistas CSV."""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.gui.responsive import fit_to_screen


class ImportantFieldsDialog(QDialog):
    """Selector de columnas; solo cambia la presentación de la tabla."""

    selectionChanged = Signal(set)

    def __init__(
        self,
        columns: Iterable[str],
        selected: Iterable[str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Seleccionar campos importantes")
        # En una pantalla baja el alto pedido no cabe y los botones de
        # aceptar y cancelar quedan por debajo del borde.
        fit_to_screen(self, 420, 520)
        self.columns = list(columns)
        self.checks: dict[str, QCheckBox] = {}
        # Marcar en bloque no debe emitir una selección por casilla: cada
        # emisión guarda el archivo y repinta el visor.
        self._bulk_update = False
        self._build_ui(set(selected))

    def _build_ui(self, selected: set[str]) -> None:
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Marque las columnas que deben aparecer en la vista de campos "
            "importantes y como recuadros en la vista previa del PDF. La "
            "selección se guarda para la plantilla activa; el CSV "
            "guardado no se modifica."
        )
        # Sin ajuste de línea el párrafo entero era el ancho mínimo del
        # diálogo: pedía 420 px y se abría con más de 1200.
        intro.setWordWrap(True)
        layout.addWidget(intro)
        bulk_row = QHBoxLayout()
        select_all = QPushButton("Marcar todas")
        select_all.setToolTip("Marcar todas las columnas de la lista")
        select_all.clicked.connect(lambda: self._set_all(True))
        bulk_row.addWidget(select_all)
        clear_all = QPushButton("Desmarcar todas")
        clear_all.setToolTip("Desmarcar todas las columnas de la lista")
        clear_all.clicked.connect(lambda: self._set_all(False))
        bulk_row.addWidget(clear_all)
        bulk_row.addStretch()
        layout.addLayout(bulk_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        for column in self.columns:
            check = QCheckBox(column)
            check.setChecked(column in selected)
            check.toggled.connect(self._emit_selection)
            self.checks[column] = check
            body_layout.addWidget(check)
        body_layout.addStretch()
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Apply
            | QDialogButtonBox.StandardButton.Close
        )
        buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(
            self._emit_selection
        )
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _set_all(self, checked: bool) -> None:
        self._bulk_update = True
        try:
            for check in self.checks.values():
                check.setChecked(checked)
        finally:
            self._bulk_update = False
        self._emit_selection()

    def _emit_selection(self, *_args) -> None:
        if self._bulk_update:
            return
        selected = {
            column for column, check in self.checks.items() if check.isChecked()
        }
        self.selectionChanged.emit(selected)

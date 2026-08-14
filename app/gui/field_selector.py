"""Selector reutilizable de columnas importantes para las dos vistas CSV."""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


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
        self.resize(420, 520)
        self.columns = list(columns)
        self.checks: dict[str, QCheckBox] = {}
        self._build_ui(set(selected))

    def _build_ui(self, selected: set[str]) -> None:
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Marque las columnas que deben aparecer en la vista de campos "
                "importantes. El CSV guardado no se modifica."
            )
        )
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

    def _emit_selection(self, *_args) -> None:
        selected = {
            column for column, check in self.checks.items() if check.isChecked()
        }
        self.selectionChanged.emit(selected)

"""Editor pequeño y autocontenido para la lista portable de aviones."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.gui.responsive import fit_to_screen
from app.utils.fleet import FLEET_FILENAME, load_fleet, normalise_matricula


class FleetStore:
    """Lee y escribe ``fleet.json`` sin depender de la plantilla OCR."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load(self) -> list[str]:
        return load_fleet(self.path)

    def save(self, matriculas: list[str]) -> None:
        values = sorted({normalise_matricula(value) for value in matriculas if value})
        self.path.write_text(
            json.dumps(
                {"version": 1, "matriculas": values},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

class FleetEditorDialog(QDialog):
    """Permite editar la lista sin abrir un editor de texto."""

    def __init__(self, store: FleetStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.store = store
        self.setWindowTitle("Lista de flota")
        fit_to_screen(self, 430, 440)
        self._build_ui()
        self._load_values()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Esta lista debe tener todos los aviones de la flota, sin "
            "faltar ninguno. Al verificar matrículas, la lectura que no "
            "aparezca aquí se reclasifica como la matrícula más parecida "
            "de la lista: el avión que falte termina clasificado como "
            "otro.\n"
            f"Archivo: {self.store.path}"
        )
        intro.setWordWrap(True)
        intro.setObjectName("fleetReminder")
        layout.addWidget(intro)

        add_row = QHBoxLayout()
        self.entry = QLineEdit()
        self.entry.setPlaceholderText("Agregar o buscar: HP-1234CMP")
        self.entry.textChanged.connect(self._filter_values)
        self.entry.returnPressed.connect(self._add_value)
        add_row.addWidget(self.entry, 1)
        add = QPushButton("Agregar")
        add.clicked.connect(self._add_value)
        add_row.addWidget(add)
        layout.addLayout(add_row)

        self.values = QListWidget()
        self.values.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        layout.addWidget(self.values, 1)

        remove = QPushButton("Quitar seleccionadas")
        remove.clicked.connect(self._remove_selected)
        layout.addWidget(remove)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _load_values(self) -> None:
        self.values.clear()
        self.values.addItems(self.store.load())

    def _filter_values(self, text: str) -> None:
        query = self._search_key(text)
        self.values.clearSelection()
        for index in range(self.values.count()):
            item = self.values.item(index)
            item.setHidden(query not in self._search_key(item.text()))

    @staticmethod
    def _search_key(value: str) -> str:
        return "".join(
            character
            for character in value.upper()
            if not character.isspace() and character != "-"
        )

    def _add_value(self) -> None:
        value = normalise_matricula(self.entry.text())
        if not value:
            QMessageBox.warning(
                self,
                "Matrícula inválida",
                "Use el formato HP-1234CMP o HP-1234WWP.",
            )
            return
        if not self.values.findItems(value, Qt.MatchFlag.MatchExactly):
            self.values.addItem(value)
            self.values.sortItems()
        self.entry.clear()

    def _remove_selected(self) -> None:
        for item in self.values.selectedItems():
            self.values.takeItem(self.values.row(item))

    def _save_and_accept(self) -> None:
        values = [self.values.item(i).text() for i in range(self.values.count())]
        try:
            self.store.save(values)
        except OSError as exc:
            QMessageBox.critical(self, "No se pudo guardar", str(exc))
            return
        self.accept()

"""Editor pequeño y autocontenido para la lista portable de matrícula."""

from __future__ import annotations

import json
import re
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


FLEET_FILENAME = "fleet.json"
_MATRICULA_RE = re.compile(r"^HP-\d{4}(?:CMP|WWP)$", re.IGNORECASE)


class FleetStore:
    """Lee y escribe ``fleet.json`` sin depender de la plantilla OCR."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load(self) -> list[str]:
        if not self.path.is_file():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return []
        values = payload.get("matriculas", []) if isinstance(payload, dict) else payload
        if not isinstance(values, list):
            return []
        result = {normalise_matricula(str(value)) for value in values}
        return sorted(value for value in result if value)

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


def normalise_matricula(value: str) -> str:
    """Normaliza la matrícula a la representación canónica de la aplicación."""
    value = str(value).strip().upper().replace(" ", "")
    if value and not value.startswith("HP-") and value.isdigit():
        value = f"HP-{value}CMP"
    elif value.startswith("HP") and not value.startswith("HP-"):
        value = "HP-" + value[2:]
    return value if _MATRICULA_RE.fullmatch(value) else ""


class FleetEditorDialog(QDialog):
    """Permite editar la lista sin abrir un editor de texto."""

    def __init__(self, store: FleetStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.store = store
        self.setWindowTitle("Lista de flota")
        self.resize(430, 440)
        self._build_ui()
        self._load_values()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Mantenga actualizada la lista de matrículas autorizadas.\n"
            f"Archivo: {self.store.path}"
        )
        intro.setWordWrap(True)
        intro.setObjectName("fleetReminder")
        layout.addWidget(intro)

        add_row = QHBoxLayout()
        self.entry = QLineEdit()
        self.entry.setPlaceholderText("Ejemplo: HP-1234CMP")
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

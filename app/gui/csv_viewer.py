"""Visor de solo lectura para reportes CSV ya procesados."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.gui.csv_utils import (
    csv_field_id,
    find_csv_files,
    important_csv_columns,
    important_field_ids_for_csv,
    read_csv_file,
)


_STATUS_COLORS = {
    "OK": "#1a7f37",
    "WARNING": "#9a6700",
    "ERROR": "#cf222e",
}
_ASSETS = Path(__file__).resolve().parents[2] / "assets"


def apply_csv_column_visibility(
    table: QTableWidget,
    columns: Iterable[str],
    important_field_ids: Iterable[str],
    important_only: bool,
) -> None:
    """Aplica el modo resumido/completo ocultando columnas de la tabla."""
    column_list = list(columns)
    visible = set(important_csv_columns(column_list, important_field_ids))
    for index, column in enumerate(column_list):
        table.setColumnHidden(index, important_only and column not in visible)


class CsvColumnModeButton(QToolButton):
    """Selector compacto del conjunto de columnas visible."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(True)
        self.setObjectName("csvColumnToggle")
        self.setAutoRaise(True)
        self.setFixedSize(30, 30)
        self.setIconSize(QSize(20, 20))
        self.setAccessibleName("Columnas visibles del CSV")
        self.toggled.connect(self._sync_visuals)
        self._sync_visuals(True)

    def _sync_visuals(self, important_only: bool) -> None:
        icon_name = "columns_important.svg" if important_only else "columns_all.svg"
        self.setIcon(QIcon(str(_ASSETS / icon_name)))
        self.setToolTip(
            "Mostrando campos importantes. Clic para mostrar todas las columnas."
            if important_only
            else "Mostrando el CSV completo. Clic para mostrar solo los "
                 "campos importantes."
        )
        self.setAccessibleDescription(self.toolTip())


class CsvViewerWindow(QMainWindow):
    """Ventana independiente que visualiza CSV de corridas procesadas."""

    def __init__(self, start_folder: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._start_folder = Path(start_folder)
        self._folder: Path | None = None
        self._columns: list[str] = []
        self._rows: list[dict[str, str]] = []
        self._important_field_ids: set[str] = set()

        self.setWindowTitle("Visor de CSV procesados")
        self.resize(1180, 720)
        self._build_ui()

    def _build_ui(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel("Carpeta procesada:"))
        self.folder_edit = QLineEdit()
        self.folder_edit.setReadOnly(True)
        self.folder_edit.setPlaceholderText("Seleccione una carpeta de output")
        folder_row.addWidget(self.folder_edit, 1)
        browse = QPushButton("Seleccionar carpeta…")
        browse.clicked.connect(self.browse_for_folder)
        folder_row.addWidget(browse)
        layout.addLayout(folder_row)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Archivo CSV:"))
        self.csv_combo = QComboBox()
        self.csv_combo.setEnabled(False)
        self.csv_combo.currentIndexChanged.connect(self._on_csv_changed)
        controls.addWidget(self.csv_combo, 1)
        self.column_toggle = CsvColumnModeButton()
        self.column_toggle.setEnabled(False)
        self.column_toggle.setVisible(False)
        self.column_toggle.toggled.connect(self._apply_column_mode)
        controls.addWidget(self.column_toggle)
        layout.addLayout(controls)

        self.table = QTableWidget(0, 0)
        self.table.setAccessibleName("CSV procesado")
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 1)

        self.status_label = QLabel("Seleccione una carpeta para visualizar su CSV.")
        self.status_label.setStyleSheet("color: #57606a;")
        layout.addWidget(self.status_label)

    def browse_for_folder(self) -> None:
        initial = self._folder or self._start_folder
        selected = QFileDialog.getExistingDirectory(
            self, "Seleccionar carpeta procesada", str(initial)
        )
        if selected:
            self.load_folder(Path(selected))

    def load_folder(self, folder: Path) -> bool:
        """Carga los CSV encontrados en ``folder`` y muestra el primero."""
        csv_paths = find_csv_files(folder)
        if not csv_paths:
            QMessageBox.information(
                self,
                "Sin archivos CSV",
                "No se encontraron archivos CSV en la carpeta seleccionada.",
            )
            return False

        self._folder = Path(folder)
        self.folder_edit.setText(str(self._folder))
        self.folder_edit.setToolTip(str(self._folder))
        self.csv_combo.blockSignals(True)
        self.csv_combo.clear()
        for path in csv_paths:
            try:
                label = str(path.relative_to(self._folder))
            except ValueError:
                label = path.name
            self.csv_combo.addItem(label, str(path))
        self.csv_combo.blockSignals(False)
        self.csv_combo.setEnabled(True)
        self.csv_combo.setCurrentIndex(0)
        self._load_csv(csv_paths[0])
        return True

    def _on_csv_changed(self, index: int) -> None:
        if index >= 0:
            path = self.csv_combo.itemData(index)
            if path:
                self._load_csv(Path(path))

    def _load_csv(self, path: Path) -> None:
        try:
            columns, rows = read_csv_file(path)
        except (OSError, ValueError, csv.Error) as exc:
            QMessageBox.critical(
                self, "No se pudo abrir el CSV", f"{path}\n\n{exc}"
            )
            return

        self._columns = columns
        self._rows = rows
        self._important_field_ids = important_field_ids_for_csv(path, columns)
        self._populate_table()
        self.column_toggle.setEnabled(True)
        self.column_toggle.setVisible(bool(columns))
        self._apply_column_mode()
        self.setWindowTitle(f"Visor de CSV — {path.name}")

    def _populate_table(self) -> None:
        self.table.setUpdatesEnabled(False)
        self.table.setSortingEnabled(False)
        try:
            self.table.clear()
            self.table.setColumnCount(len(self._columns))
            self.table.setHorizontalHeaderLabels(self._columns)
            self.table.setRowCount(len(self._rows))
            for row_index, row in enumerate(self._rows):
                for column_index, column in enumerate(self._columns):
                    item = QTableWidgetItem(row.get(column, ""))
                    status = self._status_for(row, column)
                    if status:
                        comment = row.get(f"{csv_field_id(column, self._columns)}_comment")
                        item.setToolTip(
                            f"Estado: {status}"
                            + (f"\n{comment}" if comment else "")
                        )
                        item.setForeground(Qt.GlobalColor.white)
                        item.setBackground(QColor(_STATUS_COLORS[status]))
                    self.table.setItem(row_index, column_index, item)
        finally:
            self.table.setUpdatesEnabled(True)
        self.table.resizeColumnsToContents()

    def _status_for(self, row: dict[str, str], column: str) -> str | None:
        field_id = csv_field_id(column, self._columns)
        if not field_id:
            return None
        status = row.get(f"{field_id}_status", "").upper()
        if status in _STATUS_COLORS:
            return status
        if field_id.endswith("_signature"):
            value = row.get(field_id, "").strip().lower()
            return {"true": "OK", "unclear": "WARNING", "false": "ERROR"}.get(
                value
            )
        return None

    def _apply_column_mode(self, _checked: bool | None = None) -> None:
        important_only = self.column_toggle.isChecked()
        apply_csv_column_visibility(
            self.table,
            self._columns,
            self._important_field_ids,
            important_only,
        )
        visible = (
            len(important_csv_columns(self._columns, self._important_field_ids))
            if important_only
            else len(self._columns)
        )
        self.status_label.setText(
            f"{len(self._rows)} fila(s) · {visible} de {len(self._columns)} "
            "columnas visibles · solo lectura"
        )

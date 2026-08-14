"""Visor de solo lectura para reportes CSV ya procesados."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Iterable

from PySide6.QtCore import QRegularExpression, QSize, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QIcon,
    QImage,
    QIntValidator,
    QPixmap,
    QRegularExpressionValidator,
)
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QScrollArea,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QSplitter,
    QWidget,
)

from app.gui.csv_utils import (
    csv_field_id,
    find_csv_files,
    important_csv_columns,
    important_field_ids_for_csv,
    read_csv_file,
)
from app.gui.field_selector import ImportantFieldsDialog


_STATUS_COLORS = {
    "OK": "#1a7f37",
    "WARNING": "#9a6700",
    "ERROR": "#cf222e",
}
_ASSETS = Path(__file__).resolve().parents[2] / "assets"


def source_pdf_paths_for_rows(
    csv_path: Path, rows: Iterable[dict[str, str]]
) -> list[Path | None]:
    """Resuelve el PDF fuente de cada fila usando el JSON consolidado.

    El CSV guarda solo el nombre del archivo. El JSON compañero conserva la
    ruta completa y el orden exacto reporte/página, por lo que también evita
    ambigüedad cuando se procesaron varios PDF con el mismo nombre.
    """
    row_list = list(rows)
    csv_path = Path(csv_path)
    companion = csv_path.with_suffix(".json")
    if not companion.is_file() and csv_path.stem.casefold().endswith("_completo"):
        companion = csv_path.with_name(
            f"{csv_path.stem[:-len('_completo')]}.json"
        )
    flattened: list[tuple[str, str, Path]] = []
    try:
        payload = json.loads(companion.read_text(encoding="utf-8"))
        reports = payload.get("reportes", [])
        for report in reports:
            pdf_path = Path(str(report.get("pdf_path", "")))
            filename = pdf_path.name.casefold()
            for page in report.get("pages", []):
                flattened.append(
                    (filename, str(page.get("page_number", "")), pdf_path)
                )
    except (OSError, ValueError, TypeError, AttributeError):
        flattened = []

    # La escritura consolidada recorre reportes y páginas en este mismo orden.
    if len(flattened) == len(row_list) and all(
        source_name == row.get("file", "").casefold()
        and source_page == row.get("page", "")
        for row, (source_name, source_page, _path) in zip(row_list, flattened)
    ):
        return [entry[2] for entry in flattened]

    by_key: dict[tuple[str, str], list[Path]] = {}
    for filename, page, pdf_path in flattened:
        by_key.setdefault((filename, page), []).append(pdf_path)
    resolved: list[Path | None] = []
    for row in row_list:
        candidates = by_key.get(
            (row.get("file", "").casefold(), row.get("page", "")), []
        )
        resolved.append(candidates[0] if len(candidates) == 1 else None)
    return resolved


def apply_csv_column_visibility(
    table: QTableWidget,
    columns: Iterable[str],
    important_field_ids: Iterable[str],
    important_only: bool,
    selected_columns: Iterable[str] | None = None,
) -> None:
    """Aplica el modo resumido/completo ocultando columnas de la tabla."""
    column_list = list(columns)
    visible = set(
        selected_columns
        if selected_columns is not None
        else important_csv_columns(column_list, important_field_ids)
    )
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


class ImportantFieldsButton(QToolButton):
    """Botón compacto para abrir el selector de columnas importantes."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setText("☷")
        self.setToolTip("Seleccionar campos importantes")
        self.setAccessibleName("Seleccionar campos importantes")
        self.setFixedSize(30, 30)
        self.setAutoRaise(True)


class EmbeddedPdfViewer(QFrame):
    """Visor PDF liviano para la ventana de CSV ya procesados."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("embeddedPdfPane")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self._paths: list[Path] = []
        self._path: Path | None = None
        self._page = 1
        self._total = 0
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        controls.addWidget(QLabel("PDF:"))
        self.pdf_name = QLabel("Ninguno")
        self.pdf_name.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        controls.addWidget(self.pdf_name, 1)
        self.prev = QPushButton("‹")
        self.prev.setToolTip("Página anterior")
        self.prev.clicked.connect(lambda: self.show_page(self._page - 1))
        controls.addWidget(self.prev)
        controls.addWidget(QLabel("Página"))
        self.page_edit = QLineEdit()
        self.page_edit.setValidator(QIntValidator(1, 1, self.page_edit))
        self.page_edit.setFixedWidth(65)
        self.page_edit.editingFinished.connect(self._jump)
        controls.addWidget(self.page_edit)
        self.total_pages = QLabel("de 0")
        controls.addWidget(self.total_pages)
        self.next = QPushButton("›")
        self.next.setToolTip("Página siguiente")
        self.next.clicked.connect(lambda: self.show_page(self._page + 1))
        controls.addWidget(self.next)
        layout.addLayout(controls)

        self.scroll = QScrollArea()
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setWidgetResizable(False)
        self.image = QLabel("Seleccione un PDF")
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll.setWidget(self.image)
        layout.addWidget(self.scroll, 1)
        self._sync_controls()

    def load_paths(self, paths: Iterable[Path]) -> None:
        unique: list[Path] = []
        seen: set[str] = set()
        for path in paths:
            path = Path(path)
            if not path.is_file() or path.suffix.lower() != ".pdf":
                continue
            key = str(path.resolve()).casefold()
            if key not in seen:
                seen.add(key)
                unique.append(path)
        self._paths = unique
        if self._paths:
            self.show_page(1, self._paths[0])
        else:
            self._path = None
            self._page = 1
            self._total = 0
            self.image.clear()
            self.image.setText("No se encontraron PDFs procesados")
            self._sync_controls()

    def _jump(self) -> None:
        try:
            page = int(self.page_edit.text())
        except ValueError:
            return
        self.show_page(page)

    def show_page(self, page: int, path: Path | None = None) -> None:
        path = Path(path) if path is not None else self._path
        if path is None or not path.is_file():
            return
        try:
            from app.vision.pdf_loader import page_count, render_page
            import cv2

            total = page_count(path)
            page = max(1, min(int(page), total))
            image = render_page(path, page, dpi=120)
            rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            height, width, channels = rgb.shape
            qimage = QImage(
                rgb.data, width, height, channels * width,
                QImage.Format.Format_RGB888,
            ).copy()
        except Exception as exc:  # noqa: BLE001 - visor no crítico
            self.image.clear()
            self.image.setText(f"No se pudo mostrar el PDF: {exc}")
            return
        self._path = path
        self._total = total
        self._page = page
        self.page_edit.setText(str(page))
        validator = self.page_edit.validator()
        if isinstance(validator, QIntValidator):
            validator.setTop(max(1, total))
        self.image.setPixmap(QPixmap.fromImage(qimage))
        self._sync_controls()

    def _sync_controls(self) -> None:
        self.pdf_name.setText(self._path.name if self._path else "Ninguno")
        self.pdf_name.setToolTip(str(self._path) if self._path else "")
        self.total_pages.setText(f"de {self._total}")
        self.page_edit.setEnabled(bool(self._path))
        self.prev.setEnabled(bool(self._path) and self._page > 1)
        self.next.setEnabled(bool(self._path) and self._page < self._total)


class CsvViewerWindow(QMainWindow):
    """Ventana independiente que visualiza CSV de corridas procesadas."""

    def __init__(self, start_folder: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._start_folder = Path(start_folder)
        self._folder: Path | None = None
        self._columns: list[str] = []
        self._rows: list[dict[str, str]] = []
        self._important_field_ids: set[str] = set()
        self._selected_important_columns: set[str] = set()
        self._row_pdf_paths: list[Path | None] = []
        self._search_matches: list[int] = []
        self._search_position = -1

        self.setWindowTitle("Visor de CSV procesados")
        self.resize(1180, 720)
        self.setStyleSheet(
            "QMainWindow, QWidget { font-family: 'Segoe UI', sans-serif; font-size: 10pt; }"
            "QHeaderView::section { background: #eef2f6; padding: 6px 8px;"
            " font-weight: 600; border: 0; border-bottom: 1px solid #c9d1d9; }"
            "QTableWidget { alternate-background-color: #f6f8fa; }"
        )
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
        self.important_fields_button = ImportantFieldsButton()
        self.important_fields_button.setEnabled(False)
        self.important_fields_button.setVisible(False)
        self.important_fields_button.clicked.connect(self._open_field_selector)
        controls.addWidget(self.important_fields_button)
        layout.addLayout(controls)

        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Número de bitácora:"))
        self.log_search = QLineEdit()
        self.log_search.setPlaceholderText("7 dígitos")
        self.log_search.setMaxLength(7)
        self.log_search.setValidator(
            QRegularExpressionValidator(QRegularExpression(r"\d{0,7}"), self)
        )
        self.log_search.returnPressed.connect(self._find_log_number)
        search_row.addWidget(self.log_search)
        search = QPushButton("Buscar")
        search.clicked.connect(self._find_log_number)
        search_row.addWidget(search)
        self.search_prev = QPushButton("‹")
        self.search_prev.setToolTip("Coincidencia anterior")
        self.search_prev.clicked.connect(lambda: self._move_search(-1))
        search_row.addWidget(self.search_prev)
        self.search_next = QPushButton("›")
        self.search_next.setToolTip("Coincidencia siguiente")
        self.search_next.clicked.connect(lambda: self._move_search(1))
        search_row.addWidget(self.search_next)
        self.search_context = QLabel("Ingrese exactamente 7 dígitos.")
        self.search_context.setStyleSheet("color: #57606a;")
        search_row.addWidget(self.search_context, 1)
        layout.addLayout(search_row)

        splitter = QSplitter(Qt.Orientation.Vertical)
        self.content_splitter = splitter
        self.table = QTableWidget(0, 0)
        self.table.setAccessibleName("CSV procesado")
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionsMovable(False)
        self.table.horizontalHeader().setFixedHeight(30)
        splitter.addWidget(self.table)
        self.pdf_viewer = EmbeddedPdfViewer()
        splitter.addWidget(self.pdf_viewer)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([1, 1])
        layout.addWidget(splitter, 1)

        self.status_label = QLabel("Seleccione una carpeta para visualizar su CSV.")
        self.status_label.setStyleSheet("color: #57606a;")
        layout.addWidget(self.status_label)

    def showEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().showEvent(event)
        QTimer.singleShot(0, self._balance_content_splitter)

    def resizeEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().resizeEvent(event)
        if hasattr(self, "content_splitter"):
            QTimer.singleShot(0, self._balance_content_splitter)

    def _balance_content_splitter(self) -> None:
        """Mantiene las dos zonas visuales con la misma altura."""
        available = max(
            0,
            self.content_splitter.height()
            - self.content_splitter.handleWidth(),
        )
        half = available // 2
        self.content_splitter.setSizes([half, available - half])

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
        self._selected_important_columns = set(
            important_csv_columns(columns, self._important_field_ids)
        )
        self._populate_table()
        self.column_toggle.setEnabled(True)
        self.column_toggle.setVisible(bool(columns))
        self.important_fields_button.setEnabled(bool(columns))
        self.important_fields_button.setVisible(bool(columns))
        self._apply_column_mode()
        self._load_pdf_paths(path)
        self._search_matches = []
        self._search_position = -1
        self._sync_search_controls()
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
                    item.setData(Qt.ItemDataRole.UserRole, row_index)
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
        self.table.setSortingEnabled(True)

    def _load_pdf_paths(self, csv_path: Path) -> None:
        self._row_pdf_paths = source_pdf_paths_for_rows(csv_path, self._rows)
        paths = list(csv_path.parent.parent.rglob("*.pdf"))
        paths.extend(csv_path.parent.rglob("*.pdf"))
        paths.extend(path for path in self._row_pdf_paths if path is not None)
        for row in self._rows:
            candidate = Path(row.get("file", ""))
            if candidate.is_file():
                paths.append(candidate)
        self.pdf_viewer.load_paths(paths)

    def _find_log_number(self) -> None:
        value = self.log_search.text().strip()
        if len(value) != 7 or not value.isdigit():
            self._search_matches = []
            self._search_position = -1
            self.search_context.setText("La bitácora debe tener exactamente 7 dígitos.")
            self._sync_search_controls()
            return
        self._search_matches = [
            index
            for index, row in enumerate(self._rows)
            if row.get("log_number", "").strip() == value
        ]
        self._search_position = 0 if self._search_matches else -1
        if not self._search_matches:
            self.search_context.setText(f"Bitácora {value}: sin coincidencias.")
            self._sync_search_controls()
            return
        self._show_search_match()

    def _move_search(self, offset: int) -> None:
        if not self._search_matches:
            return
        self._search_position = (
            self._search_position + offset
        ) % len(self._search_matches)
        self._show_search_match()

    def _show_search_match(self) -> None:
        source_row = self._search_matches[self._search_position]
        display_row = next(
            (
                row
                for row in range(self.table.rowCount())
                if self.table.item(row, 0)
                and self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
                == source_row
            ),
            -1,
        )
        log_column = self._columns.index("log_number")
        if display_row >= 0:
            self.table.selectRow(display_row)
            self.table.setCurrentCell(display_row, log_column)
            self.table.scrollToItem(self.table.item(display_row, log_column))

        row = self._rows[source_row]
        try:
            page = int(row.get("page", ""))
        except ValueError:
            page = 0
        path = (
            self._row_pdf_paths[source_row]
            if source_row < len(self._row_pdf_paths)
            else None
        )
        if path is not None and page > 0 and path.is_file():
            self.pdf_viewer.show_page(page, path)
            location = f"{path.name}, página {page}"
        else:
            location = f"{row.get('file', 'PDF desconocido')}, página {page or '?'}"
        self.search_context.setText(
            f"Coincidencia {self._search_position + 1} de "
            f"{len(self._search_matches)} · {location}"
        )
        self._sync_search_controls()

    def _sync_search_controls(self) -> None:
        multiple = len(self._search_matches) > 1
        self.search_prev.setEnabled(multiple)
        self.search_next.setEnabled(multiple)

    def _open_field_selector(self) -> None:
        dialog = ImportantFieldsDialog(
            self._columns, self._selected_important_columns, self
        )
        dialog.selectionChanged.connect(self._set_important_columns)
        dialog.exec()

    def _set_important_columns(self, columns: set[str]) -> None:
        self._selected_important_columns = set(columns)
        self._apply_column_mode()

    def _status_for(self, row: dict[str, str], column: str) -> str | None:
        if column == "dup":
            return "WARNING" if row.get(column, "").lower() == "true" else None
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
            self._selected_important_columns,
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

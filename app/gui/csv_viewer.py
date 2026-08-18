"""Visor de solo lectura para reportes CSV ya procesados."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Iterable

from loguru import logger
from PySide6.QtCore import (
    QEvent,
    QObject,
    QRegularExpression,
    QSize,
    Qt,
    QThread,
    QTimer,
    Signal,
)
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
    QGridLayout,
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
    QSplitter,
    QWidget,
)

from app.gui.csv_utils import (
    csv_field_id,
    find_csv_files,
    important_csv_columns,
    important_field_ids_for_csv,
    read_csv_file,
    template_for_csv,
    template_name_for_csv,
)
from app.gui.export_options import ExportOptionsGroup
from app.gui.field_selector import ImportantFieldsDialog
from app.gui.responsive import ROOMY, Density, density_for, fit_to_screen
from app.gui.table_sort import ColumnSortController
from app.reports.csv_reporter import CsvReporter
from app.gui.widgets import (
    APP_CHROME_QSS,
    DATA_TABLE_QSS,
    PANE_BG,
    PANE_BORDER,
    PANE_CONTROL_BG,
    PANE_CONTROL_HOVER,
    PANE_STATUS_COLORS,
    PANE_SURFACE_BG,
    PANE_TEXT,
    TABLE_RADIUS,
    TABLE_SELECTION_BG,
    ZOOM_OVERLAY_QSS,
    ZoomableScrollArea,
    ZoomOverlay,
    scrollbars_qss,
    style_data_table,
    style_dark_pane,
    style_pdf_surface,
)
from app.utils.important_fields import (
    IMPORTANT_FIELDS_FILENAME,
    ImportantFieldsStore,
)


_STATUS_COLORS = {
    "OK": "#1a7f37",
    "WARNING": "#9a6700",
    "ERROR": "#cf222e",
}
_PROGRAM_DIR = Path(__file__).resolve().parents[2]
_ASSETS = _PROGRAM_DIR / "assets"
_RENDER_DPI = 150
_MIN_ZOOM = 0.4
_MAX_ZOOM = 4.0
# Celdas por tramo al llenar la tabla. Medido: ~16 µs por celda, así que un
# CSV completo (85 columnas) cuesta ~800 ms de una sentada y la ventana se
# queda sin responder mientras tanto. El primer tramo se llena en el acto y
# el resto se reparte, de modo que un CSV corto sigue estando listo al volver.
_TABLE_CELL_CHUNK = 2000
# Filas que examina Qt al ajustar el ancho de una columna. Sin tope recorre
# todas: con miles de filas el ajuste solo costaba más que llenar la tabla.
_RESIZE_PRECISION = 64
# Reparto del ancho entre el visor de PDF y la tabla, el mismo de la vista
# previa y la tabla de la ventana principal.
_PDF_PANE_SHARE = 2
_TABLE_SHARE = 3
# El mismo texto que en la ventana principal; el botón lo recupera cuando
# deja de explicar por qué no se puede exportar.
_EXPORT_TOOLTIP = (
    "Volver a generar CSV, JSON y PDFs de esta corrida con las opciones "
    "actuales, sin reprocesar los archivos. Los PDFs ya exportados se "
    "conservan: los nuevos se numeran (-2, -3…) si el nombre se repite"
)
# El panel se estiliza a sí mismo para verse igual dentro y fuera del visor.
_PDF_PANE_QSS = (
    # El panel va en el mismo gris oscuro que la tabla a la que acompaña: en
    # blanco quedaba como un bloque luminoso al lado de ella.
    f"#embeddedPdfPane {{ background: {PANE_BG};"
    f" border: 1px solid {PANE_SURFACE_BG}; border-radius: {TABLE_RADIUS}px; }}"
    # Mismo radio que el marco que la contiene: quedaba en 4px, un cuadro
    # distinto al del resto de la aplicacion (QGroupBox, tablas, timeSummary).
    f"#pdfSurface {{ background: {PANE_SURFACE_BG};"
    f" border: 1px solid {PANE_BORDER}; border-radius: {TABLE_RADIUS}px; }}"
    # Sin fondo explícito, la etiqueta pinta el color de ventana y tapa la
    # superficie oscura justo cuando solo muestra el mensaje de estado.
    f"#pdfPage {{ color: {PANE_TEXT}; padding: 12px; background: transparent; }}"
    f"#embeddedPdfPane QLabel {{ color: {PANE_TEXT}; background: transparent; }}"
    # Los campos suben un escalón sobre el panel para seguir leyéndose como
    # controles y no como parte del fondo.
    "#embeddedPdfPane QComboBox, #embeddedPdfPane QLineEdit,"
    "#embeddedPdfPane QPushButton, #embeddedPdfPane QToolButton {"
    f" background: {PANE_CONTROL_BG}; color: {PANE_TEXT};"
    f" border: 1px solid {PANE_BORDER}; border-radius: {TABLE_RADIUS}px; padding: 2px 6px; }}"
    "#embeddedPdfPane QComboBox:hover, #embeddedPdfPane QPushButton:hover,"
    "#embeddedPdfPane QToolButton:hover {"
    f" background: {PANE_CONTROL_HOVER}; }}"
    "#embeddedPdfPane QComboBox:disabled, #embeddedPdfPane QPushButton:disabled,"
    "#embeddedPdfPane QToolButton:disabled {"
    f" background: {PANE_BG}; color: #8c959f; }}"
    # La lista desplegable es una ventana aparte y no hereda el fondo.
    "#embeddedPdfPane QComboBox QAbstractItemView {"
    f" background: {PANE_CONTROL_BG}; color: {PANE_TEXT};"
    f" selection-background-color: {TABLE_SELECTION_BG}; }}"
    # El recuadro de zoom va al final: sus reglas y las del panel tienen la
    # misma especificidad y aquí gana la última.
) + scrollbars_qss("#embeddedPdfPane") + ZOOM_OVERLAY_QSS


def _join_names(names: Iterable[str], limit: int = 3) -> str:
    """Enumera nombres de archivo sin desbordar el indicador."""
    name_list = list(names)
    shown = ", ".join(name_list[:limit])
    return shown if len(name_list) <= limit else f"{shown}…"


def _companion_payload(csv_path: Path) -> dict:
    """Lee el JSON consolidado que acompaña al CSV; ``{}`` si no está."""
    csv_path = Path(csv_path)
    companion = csv_path.with_suffix(".json")
    if not companion.is_file() and csv_path.stem.casefold().endswith("_completo"):
        companion = csv_path.with_name(
            f"{csv_path.stem[:-len('_completo')]}.json"
        )
    try:
        payload = json.loads(companion.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


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
    flattened: list[tuple[str, str, Path]] = []
    try:
        for report in _companion_payload(csv_path).get("reportes", []):
            pdf_path = Path(str(report.get("pdf_path", "")))
            filename = pdf_path.name.casefold()
            for page in report.get("pages", []):
                flattened.append(
                    (filename, str(page.get("page_number", "")), pdf_path)
                )
    except (TypeError, AttributeError):
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


def source_documents_for_csv(csv_path: Path) -> list[Path]:
    """PDF que originaron el CSV, en el orden en que se procesaron."""
    documents: list[Path] = []
    seen: set[str] = set()
    try:
        recorded = [
            str(report.get("pdf_path", ""))
            for report in _companion_payload(csv_path).get("reportes", [])
        ]
    except (TypeError, AttributeError):
        return []
    for raw in recorded:
        if not raw:
            continue
        path = Path(raw)
        key = str(path).casefold()
        if key not in seen:
            seen.add(key)
            documents.append(path)
    return documents


def _documents_from_rows(rows: Iterable[dict[str, str]]) -> list[Path]:
    """Nombres de PDF declarados por el CSV cuando no hay JSON compañero."""
    documents: list[Path] = []
    seen: set[str] = set()
    for row in rows:
        name = (row.get("file") or "").strip()
        if not name.casefold().endswith(".pdf"):
            continue
        key = name.casefold()
        if key not in seen:
            seen.add(key)
            documents.append(Path(name))
    return documents


def _locate_document(
    recorded: Path, folders: Iterable[Path], deep_folders: Iterable[Path]
) -> Path | None:
    """Busca un PDF de origen que pudo haberse movido desde el procesamiento.

    ``folders`` se revisa solo por nombre exacto; el recorrido recursivo queda
    reservado a ``deep_folders``, las carpetas que el usuario indicó a mano.
    """
    # Un nombre suelto solo vale relativo a las carpetas de la corrida: no se
    # resuelve contra el directorio de trabajo, que es arbitrario.
    if recorded.is_absolute() and recorded.is_file():
        return recorded
    name = recorded.name
    if not name:
        return None
    for folder in folders:
        candidate = Path(folder) / name
        if candidate.is_file():
            return candidate
    for folder in deep_folders:
        try:
            match = next(
                (path for path in Path(folder).rglob(name) if path.is_file()),
                None,
            )
        except OSError:
            match = None
        if match is not None:
            return match
    return None


def resolve_source_documents(
    csv_path: Path,
    rows: Iterable[dict[str, str]],
    extra_folders: Iterable[Path] = (),
) -> tuple[list[Path | None], list[Path], list[str]]:
    """Ubica en disco los PDF de origen del CSV.

    Devuelve el PDF de cada fila, los documentos que sí están disponibles y el
    nombre de los que el CSV declara pero no se encontraron, para que el visor
    pueda mostrarlos todos o avisar cuáles faltan.
    """
    csv_path = Path(csv_path)
    row_list = list(rows)
    row_paths = source_pdf_paths_for_rows(csv_path, row_list)
    recorded = source_documents_for_csv(csv_path) or _documents_from_rows(row_list)
    extra = [Path(folder) for folder in extra_folders]
    folders = [csv_path.parent, csv_path.parent.parent, _PROGRAM_DIR / "input"]
    folders.extend(extra)

    available: list[Path] = []
    seen: set[str] = set()
    missing: list[str] = []
    by_recorded: dict[str, Path] = {}
    by_name: dict[str, Path | None] = {}
    for source in recorded:
        found = _locate_document(source, folders, extra)
        if found is None:
            if source.name not in missing:
                missing.append(source.name)
            continue
        key = str(found.resolve()).casefold()
        if key not in seen:
            seen.add(key)
            available.append(found)
        by_recorded[str(source).casefold()] = found
        # Un nombre repetido entre documentos distintos no identifica una fila.
        name = source.name.casefold()
        by_name[name] = None if name in by_name else found

    resolved: list[Path | None] = []
    for row, path in zip(row_list, row_paths):
        if path is not None:
            resolved.append(by_recorded.get(str(path).casefold()))
        else:
            resolved.append(by_name.get((row.get("file") or "").casefold()))
    return resolved, available, missing


def run_dir_for_csv(csv_path: Path) -> Path | None:
    """Carpeta de la corrida a la que pertenece el CSV, si se reconoce.

    Las corridas guardan el reporte en ``<corrida>/datos/``; las históricas
    lo dejaban en la raíz de la corrida. Devuelve ``None`` cuando el CSV no
    está en la carpeta de su corrida, que es el único sitio sobre el que se
    puede volver a exportar.
    """
    csv_path = Path(csv_path)
    parent = csv_path.parent
    run_dir = parent.parent if parent.name.casefold() == "datos" else parent
    # La carpeta tiene que ser la de esta corrida y no una cualquiera donde
    # alguien haya dejado copias: volver a exportar limpia de ahí lo que la
    # corrida regenera, y sobre una carpeta ajena eso borraría archivos que
    # no son suyos. Una corrida siempre nombra igual su carpeta y su CSV.
    stem = csv_path.stem
    if stem.casefold().endswith("_completo"):
        stem = stem[: -len("_completo")]
    return run_dir if run_dir.name.casefold() == stem.casefold() else None


def reports_for_csv(
    csv_path: Path, extra_folders: Iterable[Path] = ()
) -> tuple[list, list[str]]:
    """Reconstruye los reportes de la corrida desde el JSON compañero.

    El JSON consolidado guarda los reportes completos, así que la corrida se
    puede volver a exportar sin repetir el OCR. Cada reporte apunta al PDF
    que lo originó: si el archivo se movió, se busca igual que lo hace el
    visor y el reporte queda apuntando a donde está ahora. Devuelve también
    el nombre de los PDF que no aparecieron, porque sin ellos no se pueden
    rehacer las páginas.
    """
    from app.models.schemas import ValidationReport

    csv_path = Path(csv_path)
    extra = [Path(folder) for folder in extra_folders]
    folders = [csv_path.parent, csv_path.parent.parent, _PROGRAM_DIR / "input"]
    folders.extend(extra)

    reports = []
    missing: list[str] = []
    for entry in _companion_payload(csv_path).get("reportes", []):
        report = ValidationReport.model_validate(entry)
        located = _locate_document(Path(report.pdf_path), folders, extra)
        if located is None:
            name = Path(report.pdf_path).name
            if name not in missing:
                missing.append(name)
            continue
        report.pdf_path = str(located)
        reports.append(report)
    return reports, missing


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


def _document_labels(documents: list[Path]) -> list[str]:
    """Etiquetas del selector; desambigua los nombres de archivo repetidos."""
    counts: dict[str, int] = {}
    for document in documents:
        counts[document.name.casefold()] = counts.get(document.name.casefold(), 0) + 1
    return [
        document.name
        if counts[document.name.casefold()] == 1
        else f"{document.parent.name}\\{document.name}"
        for document in documents
    ]


class PdfPageLoader(QObject):
    """Rasteriza páginas del PDF en su propia QThread.

    Es el mismo reparto que ya usa la vista previa de la ventana principal
    (``PreviewLoader``): rasterizar una página cuesta ~90 ms, y hacerlo en el
    hilo de interfaz congelaba la ventana una vez por cada fila que el
    usuario recorría con las flechas.
    """

    requested = Signal(str, int)
    ready = Signal(str, int, object)

    def run(self, pdf_path: str, page: int) -> None:
        import cv2

        from app.vision.pdf_loader import render_page

        try:
            image = render_page(Path(pdf_path), page, dpi=_RENDER_DPI)
            rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            height, width, channels = rgb.shape
            qimage = QImage(
                rgb.data, width, height, channels * width,
                QImage.Format.Format_RGB888,
            ).copy()
        except Exception as exc:  # noqa: BLE001 - visor no crítico
            logger.warning(
                f"No se pudo rasterizar {pdf_path} p. {page}: {exc}"
            )
            self.ready.emit(pdf_path, page, None)
            return
        self.ready.emit(pdf_path, page, qimage)


class EmbeddedPdfViewer(QFrame):
    """Visor de los PDF que originaron el CSV abierto.

    La página se escala al espacio disponible en lugar de mostrarse a tamaño
    fijo, y el estado de los documentos de origen queda siempre a la vista:
    cuáles se encontraron y cuáles no.
    """

    relocateRequested = Signal()

    def apply_density(self, density: Density) -> None:
        """Aprieta los mínimos del panel cuando la ventana es baja."""
        self._density = density
        self.setMinimumWidth(density.pdf_pane_min_width)
        self.scroll.setMinimumHeight(density.pdf_pane_min_height)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("embeddedPdfPane")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self._documents: list[Path] = []
        self._missing: list[str] = []
        self._path: Path | None = None
        self._page = 1
        self._total = 0
        self._zoom = 1.0  # 1.0 = página ajustada al panel
        self._density = ROOMY
        self._source: QPixmap | None = None
        self._refresh_pending = False
        # Páginas por documento: el recuento reabría el PDF en cada salto.
        self._page_counts: dict[str, int] = {}
        self._pending_render: tuple[str, int] | None = None
        self._loader: PdfPageLoader | None = None
        self._loader_thread: QThread | None = None
        self._build_ui()

    # ── Render en segundo plano ─────────────────────────────────────────

    def _ensure_loader(self) -> PdfPageLoader:
        """Cargador vivo en su hilo; se arranca la primera vez que hace falta."""
        if self._loader is None:
            self._loader = PdfPageLoader()
            self._loader_thread = QThread(self)
            self._loader.moveToThread(self._loader_thread)
            self._loader.requested.connect(self._loader.run)
            self._loader.ready.connect(self._on_page_ready)
        if self._loader_thread is not None and not self._loader_thread.isRunning():
            self._loader_thread.start()
        return self._loader

    def shutdown(self) -> None:
        """Detiene el hilo de render antes de que el panel se destruya.

        Destruir un ``QThread`` en marcha aborta el proceso, así que la
        ventana que contiene el panel llama aquí al cerrarse.
        """
        thread = self._loader_thread
        if thread is not None and thread.isRunning():
            thread.quit()
            thread.wait(5000)

    def closeEvent(self, event) -> None:  # noqa: N802 - API Qt
        self.shutdown()
        super().closeEvent(event)

    def _page_total(self, path: Path) -> int:
        """Páginas del documento, contadas una sola vez por archivo."""
        key = str(path)
        if key not in self._page_counts:
            from app.vision.pdf_loader import page_count

            try:
                self._page_counts[key] = max(0, page_count(path))
            except Exception:  # noqa: BLE001 - visor no crítico
                self._page_counts[key] = 0
        return self._page_counts[key]

    def _build_ui(self) -> None:
        """Misma disposición que la vista previa de la ventana principal.

        La página ocupa el panel entero con el recuadro de zoom flotando
        sobre ella y la paginación centrada en una barra debajo. El selector
        de documento se queda en su propia fila: ahí es un control con
        etiqueta, no el nombre de archivo de la ventana principal, y en el
        ancho que le toca al panel (dos quintos de la ventana) no cabe junto
        a una paginación centrada sin montarse encima de ella.
        """
        self.setStyleSheet(_PDF_PANE_QSS)
        style_dark_pane(self)
        self.setMinimumWidth(self._density.pdf_pane_min_width)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        documents = QHBoxLayout()
        documents.addWidget(QLabel("PDF de origen:"))
        self.pdf_combo = QComboBox()
        self.pdf_combo.setEnabled(False)
        self.pdf_combo.setAccessibleName("PDF de origen del CSV")
        self.pdf_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.pdf_combo.setMinimumContentsLength(16)
        self.pdf_combo.currentIndexChanged.connect(self._on_document_changed)
        documents.addWidget(self.pdf_combo, 1)
        self.locate_button = QPushButton("Ubicar PDF…")
        self.locate_button.setToolTip(
            "Indicar la carpeta donde están ahora los PDF que no se encontraron"
        )
        self.locate_button.setVisible(False)
        self.locate_button.clicked.connect(self.relocateRequested)
        documents.addWidget(self.locate_button)
        layout.addLayout(documents)

        self.scroll = ZoomableScrollArea()
        self.scroll.setObjectName("pdfSurface")
        style_pdf_surface(self.scroll)
        self.scroll.set_zoom_callback(self._zoom_by)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setWidgetResizable(False)
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll.setMinimumHeight(self._density.pdf_pane_min_height)
        self.image = QLabel()
        self.image.setObjectName("pdfPage")
        self.image.setWordWrap(True)
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll.setWidget(self.image)
        self.scroll.viewport().installEventFilter(self)

        viewer_frame = QWidget()
        viewer_frame_layout = QGridLayout(viewer_frame)
        viewer_frame_layout.setContentsMargins(0, 0, 0, 0)
        viewer_frame_layout.addWidget(self.scroll, 0, 0)

        zoom_overlay = ZoomOverlay(
            ("Acercar la página", "Acercar la página", lambda: self._zoom_by(1.25)),
            (
                "Ajustar la página al panel",
                "Ajustar la página al panel",
                self.fit_page,
            ),
            ("Alejar la página", "Alejar la página", lambda: self._zoom_by(0.8)),
            viewer_frame,
        )
        self.btn_zoom_in = zoom_overlay.btn_in
        self.btn_zoom_fit = zoom_overlay.btn_fit
        self.btn_zoom_out = zoom_overlay.btn_out
        self.zoom_label = zoom_overlay.value_label

        zoom_holder = QWidget(viewer_frame)
        zoom_holder_layout = QVBoxLayout(zoom_holder)
        zoom_holder_layout.setContentsMargins(8, 8, 8, 8)
        zoom_holder_layout.addWidget(zoom_overlay)
        viewer_frame_layout.addWidget(
            zoom_holder,
            0,
            0,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        )
        zoom_holder.raise_()
        layout.addWidget(viewer_frame, 1)

        self.pagination = QWidget()
        controls = QHBoxLayout(self.pagination)
        controls.setContentsMargins(0, 0, 0, 0)
        self.prev = QToolButton()
        self.prev.setArrowType(Qt.ArrowType.LeftArrow)
        self.prev.setToolTip("Página anterior")
        self.prev.setAccessibleName("Página anterior")
        self.prev.clicked.connect(lambda: self.show_page(self._page - 1))
        controls.addWidget(self.prev)
        controls.addWidget(QLabel("Página"))
        self.page_edit = QLineEdit()
        self.page_edit.setValidator(QIntValidator(1, 1, self.page_edit))
        self.page_edit.setAccessibleName("Página del PDF")
        self.page_edit.setFixedWidth(65)
        self.page_edit.editingFinished.connect(self._jump)
        controls.addWidget(self.page_edit)
        self.total_pages = QLabel("de 0")
        controls.addWidget(self.total_pages)
        self.next = QToolButton()
        self.next.setArrowType(Qt.ArrowType.RightArrow)
        self.next.setToolTip("Página siguiente")
        self.next.setAccessibleName("Página siguiente")
        self.next.clicked.connect(lambda: self.show_page(self._page + 1))
        controls.addWidget(self.next)

        # La paginación se centra sobre todo el ancho de la página, igual que
        # en la ventana principal.
        nav_bar = QWidget()
        nav_bar_layout = QGridLayout(nav_bar)
        nav_bar_layout.setContentsMargins(0, 0, 0, 0)
        nav_bar_layout.addWidget(
            self.pagination, 0, 0, Qt.AlignmentFlag.AlignCenter
        )
        layout.addWidget(nav_bar)

        self.source_status = QLabel()
        self.source_status.setObjectName("pdfSourceStatus")
        self.source_status.setWordWrap(True)
        self.source_status.setAccessibleName("Estado de los PDF de origen")
        layout.addWidget(self.source_status)

        self._update_source_status()
        self._show_placeholder(
            "Seleccione una carpeta procesada o un CSV para ver sus páginas."
        )
        self._sync_controls()

    def load_paths(
        self, paths: Iterable[Path], missing: Iterable[str] = ()
    ) -> None:
        """Publica los PDF de origen disponibles y los que no se encontraron."""
        documents: list[Path] = []
        seen: set[str] = set()
        for path in paths:
            path = Path(path)
            if not path.is_file() or path.suffix.lower() != ".pdf":
                continue
            key = str(path.resolve()).casefold()
            if key not in seen:
                seen.add(key)
                documents.append(path)
        self._documents = documents
        self._missing = [str(name) for name in missing]
        self._path = None
        self._page = 1
        self._total = 0
        self._source = None
        self._page_counts = {}
        self._pending_render = None

        labels = _document_labels(documents)
        self.pdf_combo.blockSignals(True)
        self.pdf_combo.clear()
        for index, document in enumerate(documents):
            self.pdf_combo.addItem(labels[index], str(document))
            self.pdf_combo.setItemData(
                index, str(document), Qt.ItemDataRole.ToolTipRole
            )
        self.pdf_combo.blockSignals(False)
        self.pdf_combo.setEnabled(bool(documents))
        self.locate_button.setVisible(bool(self._missing))
        self._update_source_status()

        if documents:
            self.show_page(1, documents[0])
            return
        self._show_placeholder(
            "No se encontraron los PDF de origen de este CSV."
            if self._missing
            else "Este CSV no registra los PDF de los que proviene."
        )
        self._sync_controls()

    def _on_document_changed(self, index: int) -> None:
        if 0 <= index < len(self._documents):
            self.show_page(1, self._documents[index])

    def _jump(self) -> None:
        try:
            page = int(self.page_edit.text())
        except ValueError:
            return
        self.show_page(page)

    def show_page(self, page: int, path: Path | None = None) -> None:
        """Pide una página al hilo de render; ``path`` cambia de documento.

        La navegación (número de página, total, selector y botones) se
        actualiza en el acto y la página anterior se mantiene a la vista
        hasta que llega la nueva, igual que en la vista previa principal.
        """
        path = Path(path) if path is not None else self._path
        if path is None or not path.is_file():
            return
        total = self._page_total(path)
        if not total:
            self._path = path
            self._total = 0
            self._pending_render = None
            self._show_placeholder(f"No se pudo mostrar el PDF: {path.name}")
            self._sync_combo_to(path)
            self._sync_controls()
            return
        page = max(1, min(int(page), total))
        request = (str(path), page)
        if self._pending_render == request:
            return
        if self._source is not None and path == self._path and page == self._page:
            return
        self._path = path
        self._total = total
        self._page = page
        self.page_edit.setText(str(page))
        validator = self.page_edit.validator()
        if isinstance(validator, QIntValidator):
            validator.setTop(max(1, total))
        self._sync_combo_to(path)
        self._sync_controls()
        self._pending_render = request
        self._ensure_loader().requested.emit(*request)

    def _on_page_ready(
        self, pdf_path: str, page: int, qimage: QImage | None
    ) -> None:
        """Aplica el render solo si sigue siendo la página pedida."""
        if (pdf_path, page) != self._pending_render:
            return  # respuesta obsoleta: el usuario ya cambió de fila
        self._pending_render = None
        if qimage is None:
            self._show_placeholder(
                f"No se pudo mostrar el PDF: {Path(pdf_path).name}"
            )
            self._sync_controls()
            return
        self._source = QPixmap.fromImage(qimage)
        self._render_page()
        self._sync_controls()

    def _sync_combo_to(self, path: Path) -> None:
        index = next(
            (
                position
                for position, document in enumerate(self._documents)
                if document == path
            ),
            -1,
        )
        if index >= 0 and index != self.pdf_combo.currentIndex():
            self.pdf_combo.blockSignals(True)
            self.pdf_combo.setCurrentIndex(index)
            self.pdf_combo.blockSignals(False)

    def _fit_scale(self) -> float:
        """Factor que hace caber la página completa en el panel."""
        source = self._source
        if source is None or source.isNull():
            return 1.0
        viewport = self.scroll.viewport().size()
        return max(
            0.05,
            min(
                max(1, viewport.width() - 12) / source.width(),
                max(1, viewport.height() - 12) / source.height(),
            ),
        )

    def _render_page(self) -> None:
        """Escala la página al panel sin volver a rasterizar el PDF."""
        source = self._source
        if source is None or source.isNull():
            return
        scale = self._fit_scale() * self._zoom
        pixmap = source.scaled(
            max(1, round(source.width() * scale)),
            max(1, round(source.height() * scale)),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.image.setText("")
        self.image.setPixmap(pixmap)
        self.image.setFixedSize(pixmap.size())
        self._sync_zoom_controls()

    def _show_placeholder(self, text: str) -> None:
        """Deja el panel con un mensaje centrado y sin página cargada."""
        self._source = None
        self.image.setPixmap(QPixmap())
        self.image.setText(text)
        self.image.setFixedSize(self.scroll.viewport().size())
        self._sync_zoom_controls()

    def fit_page(self) -> None:
        """Vuelve al ajuste de página completa."""
        self._zoom = 1.0
        self._render_page()

    def _zoom_by(self, factor: float) -> None:
        self._zoom = min(_MAX_ZOOM, max(_MIN_ZOOM, self._zoom * factor))
        self._render_page()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - API Qt
        """Reajusta la página ante cualquier cambio del área visible.

        El panel también cambia de tamaño sin que lo haga la ventana: basta
        con que el indicador de origen pase de una línea a dos.
        """
        if (
            watched is self.scroll.viewport()
            and event.type() == QEvent.Type.Resize
        ):
            self._schedule_refresh()
        return super().eventFilter(watched, event)

    def _schedule_refresh(self) -> None:
        if self._refresh_pending:
            return
        self._refresh_pending = True
        QTimer.singleShot(0, self._refresh_view)

    def _refresh_view(self) -> None:
        self._refresh_pending = False
        if self._source is not None:
            self._render_page()
        else:
            self.image.setFixedSize(self.scroll.viewport().size())

    def _update_source_status(self) -> None:
        """Indicador permanente del origen de las páginas mostradas."""
        available = len(self._documents)
        missing = len(self._missing)
        names = _join_names(self._missing)
        if not available and not missing:
            # Vale tanto antes de abrir un CSV como para uno sin PDF anotados;
            # el detalle lo da el mensaje del área de página.
            text = "Sin PDF de origen para mostrar."
            color = "#b0b7c0"
        elif not available:
            text = (
                f"No se encontró el PDF de origen ({names})."
                if missing == 1
                else f"No se encontraron los {missing} PDF de origen ({names})."
            ) + " Use «Ubicar PDF…»."
            color = PANE_STATUS_COLORS["ERROR"]
        elif missing:
            text = (
                f"{available} de {available + missing} PDF de origen "
                f"disponibles · {'falta' if missing == 1 else 'faltan'} {names}."
            )
            color = PANE_STATUS_COLORS["WARNING"]
        else:
            text = (
                "1 PDF de origen disponible."
                if available == 1
                else f"{available} PDF de origen disponibles."
            )
            color = PANE_STATUS_COLORS["OK"]
        self.source_status.setText(text)
        # Gana a la regla general de QLabel del panel, que va sin id.
        self.source_status.setStyleSheet(f"#pdfSourceStatus {{ color: {color}; }}")

    def _sync_zoom_controls(self) -> None:
        has_page = self._source is not None
        self.btn_zoom_out.setEnabled(has_page and self._zoom > _MIN_ZOOM)
        self.btn_zoom_in.setEnabled(has_page and self._zoom < _MAX_ZOOM)
        self.btn_zoom_fit.setEnabled(has_page)
        self.zoom_label.setText(f"{round(self._zoom * 100)}%")

    def _sync_controls(self) -> None:
        has_page = self._source is not None
        self.total_pages.setText(f"de {self._total}")
        self.page_edit.setEnabled(has_page)
        self.prev.setEnabled(has_page and self._page > 1)
        self.next.setEnabled(has_page and self._page < self._total)
        self._sync_zoom_controls()


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
        self._template_name: str | None = None
        self._important_fields_store = ImportantFieldsStore(
            _PROGRAM_DIR / IMPORTANT_FIELDS_FILENAME
        )
        self._row_pdf_paths: list[Path | None] = []
        self._pdf_search_folders: list[Path] = []
        self._search_matches: list[int] = []
        self._search_position = -1
        self._splitter_adjusted = False
        self._outputs_worker = None
        # El indicador de abajo lleva dos cosas: el resumen de la tabla, que
        # se rehace en cada cambio de columnas, y el estado de la exportación,
        # que sobrevive a esos cambios hasta que se abre otro CSV.
        self._summary = "Seleccione una carpeta o un CSV para visualizarlo."
        self._export_note = ""
        self._pending_rows: list[int] = []
        self._table_timer = QTimer(self)
        # Intervalo cero: el siguiente tramo entra cuando la cola de eventos
        # queda vacía, de modo que la ventana responde entre uno y otro.
        self._table_timer.setInterval(0)
        self._table_timer.timeout.connect(self._on_table_chunk)

        self.setWindowTitle("Visor de CSV procesados")
        # Como la ventana principal: el tamaño lo pone la pantalla. Pedía
        # 1400x840 y en un portátil de 1366x768 se abría más grande que el
        # escritorio, con la fila de exportación fuera de la vista.
        self._density = fit_to_screen(self, 1400, 840)
        # La misma hoja que la ventana principal: tipografía, botones y el
        # radio de los cuadros salen de ahí, no del estilo nativo.
        self._apply_density_stylesheet()
        self._build_ui()
        self.pdf_viewer.apply_density(self._density)

    def _build_ui(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel("Origen:"))
        self.folder_edit = QLineEdit()
        self.folder_edit.setReadOnly(True)
        self.folder_edit.setPlaceholderText(
            "Seleccione una carpeta de output o un archivo CSV"
        )
        self.folder_edit.setAccessibleName("Origen de los CSV mostrados")
        folder_row.addWidget(self.folder_edit, 1)
        browse = QPushButton("Seleccionar carpeta…")
        browse.setToolTip("Abrir una carpeta procesada y listar sus CSV")
        browse.clicked.connect(self.browse_for_folder)
        folder_row.addWidget(browse)
        browse_csv = QPushButton("Seleccionar CSV…")
        browse_csv.setToolTip("Abrir directamente un archivo CSV procesado")
        browse_csv.clicked.connect(self.browse_for_csv)
        folder_row.addWidget(browse_csv)
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

        self.export_options = ExportOptionsGroup()
        layout.addWidget(self.export_options)

        # Horizontal, y con el PDF a la izquierda igual que la vista previa de
        # la ventana principal: una página de bitácora es vertical, así
        # aprovecha todo el alto en lugar de una franja de pocos centímetros.
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.content_splitter = splitter
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(6)
        splitter.splitterMoved.connect(self._on_splitter_moved)
        self.pdf_viewer = EmbeddedPdfViewer()
        self.pdf_viewer.relocateRequested.connect(self._relocate_source_pdfs)
        splitter.addWidget(self.pdf_viewer)
        self.table = QTableWidget(0, 0)
        self.table.setAccessibleName("CSV procesado")
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        style_data_table(self.table)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionsMovable(False)
        self.table.horizontalHeader().setFixedHeight(30)
        self.table.horizontalHeader().setResizeContentsPrecision(_RESIZE_PRECISION)
        self.table_sort = ColumnSortController(self.table)
        self.table.currentCellChanged.connect(self._on_current_cell_changed)
        splitter.addWidget(self.table)
        # El mismo reparto de la ventana principal: la tabla lleva muchas
        # columnas y la página cabe entera en la parte que le toca. Los
        # factores solo gobiernan el espacio sobrante, así que el reparto se
        # aplica además a mano: si no, el panel arranca con lo que pida su
        # contenido y se lleva más de la mitad de la ventana.
        splitter.setStretchFactor(0, _PDF_PANE_SHARE)
        splitter.setStretchFactor(1, _TABLE_SHARE)
        layout.addWidget(splitter, 1)

        status_row = QHBoxLayout()
        self.status_label = QLabel(self._summary)
        self.status_label.setStyleSheet("color: #57606a;")
        status_row.addWidget(self.status_label, 1)
        self.btn_export = QPushButton("Exportar")
        self.btn_export.setEnabled(False)
        self.btn_export.setToolTip(_EXPORT_TOOLTIP)
        self.btn_export.clicked.connect(self._exportar)
        status_row.addWidget(self.btn_export)
        layout.addLayout(status_row)

    def _apply_density_stylesheet(self) -> None:
        """Hoja de la ventana con el fragmento de medidas de la densidad."""
        self.setStyleSheet(APP_CHROME_QSS + DATA_TABLE_QSS + self._density.qss)

    def _update_responsive_layout(self) -> None:
        """Aprieta o suelta las medidas según el alto que tenga la ventana."""
        density = density_for(self.height(), self._density)
        if density is self._density:
            return
        self._density = density
        self._apply_density_stylesheet()
        self.pdf_viewer.apply_density(density)

    def showEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().showEvent(event)
        self._update_responsive_layout()
        QTimer.singleShot(0, self._balance_content_splitter)

    def resizeEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().resizeEvent(event)
        if hasattr(self, "content_splitter"):
            self._update_responsive_layout()
            QTimer.singleShot(0, self._balance_content_splitter)

    def _on_splitter_moved(self, _position: int, _index: int) -> None:
        """Una vez que el usuario reparte el espacio, se respeta su medida."""
        self._splitter_adjusted = True

    def _balance_content_splitter(self) -> None:
        """Reparte el ancho entre visor y tabla mientras nadie lo ajuste."""
        if self._splitter_adjusted:
            return
        available = max(
            0,
            self.content_splitter.width() - self.content_splitter.handleWidth(),
        )
        pane = available * _PDF_PANE_SHARE // (_PDF_PANE_SHARE + _TABLE_SHARE)
        self.content_splitter.setSizes([pane, available - pane])

    def closeEvent(self, event) -> None:  # noqa: N802 - API Qt
        """Detiene el llenado y los hilos antes de cerrar.

        El panel es un hijo de la ventana: cerrarla no le entrega un evento
        de cierre, y dejar su hilo de render vivo abortaría el proceso al
        destruir la ventana. La exportación no se puede interrumpir a mitad
        de escritura, así que se la espera.
        """
        self._table_timer.stop()
        self.pdf_viewer.shutdown()
        worker = self._outputs_worker
        if worker is not None and worker.isRunning():
            worker.wait(30000)
        super().closeEvent(event)

    def browse_for_folder(self) -> None:
        initial = self._folder or self._start_folder
        selected = QFileDialog.getExistingDirectory(
            self, "Seleccionar carpeta procesada", str(initial)
        )
        if selected:
            self.load_folder(Path(selected))

    def browse_for_csv(self) -> None:
        initial = self._folder or self._start_folder
        selected, _filter = QFileDialog.getOpenFileName(
            self,
            "Seleccionar CSV procesado",
            str(initial),
            "Reportes CSV (*.csv *.CSV);;Todos los archivos (*)",
        )
        if selected:
            self.load_csv_file(Path(selected))

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
        self._show_csv_choices(Path(folder), csv_paths, csv_paths[0])
        return True

    def load_csv_file(self, csv_path: Path) -> bool:
        """Abre un CSV concreto y deja a mano los demás de su carpeta."""
        csv_path = Path(csv_path)
        if not csv_path.is_file():
            QMessageBox.information(
                self,
                "Archivo no disponible",
                f"No se encontró el archivo CSV:\n{csv_path}",
            )
            return False
        siblings = find_csv_files(csv_path.parent)
        if not any(path == csv_path for path in siblings):
            siblings = [csv_path, *siblings]
        self._show_csv_choices(csv_path.parent, siblings, csv_path)
        return True

    def _show_csv_choices(
        self, folder: Path, csv_paths: list[Path], current: Path
    ) -> None:
        """Publica los CSV disponibles y abre el indicado."""
        self._folder = Path(folder)
        self.folder_edit.setText(str(self._folder))
        self.folder_edit.setToolTip(str(self._folder))
        self._pdf_search_folders = []
        self.csv_combo.blockSignals(True)
        self.csv_combo.clear()
        for path in csv_paths:
            try:
                label = str(path.relative_to(self._folder))
            except ValueError:
                label = path.name
            self.csv_combo.addItem(label, str(path))
        index = next(
            (
                position
                for position, path in enumerate(csv_paths)
                if path == current
            ),
            0,
        )
        self.csv_combo.setCurrentIndex(index)
        self.csv_combo.blockSignals(False)
        self.csv_combo.setEnabled(True)
        self._load_csv(csv_paths[index])

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

        self._export_note = ""
        self._columns = columns
        self._rows = rows
        # Se limpia antes de poblar: la tabla emite selección mientras se
        # llena y las rutas de la corrida anterior ya no corresponden.
        self._row_pdf_paths = []
        self._important_field_ids = important_field_ids_for_csv(path, columns)
        # La selección guardada manda sobre la inferida: es la lista que el
        # usuario editó para esta plantilla, aquí o en la ventana principal.
        self._template_name = template_name_for_csv(path)
        stored = self._important_fields_store.load(self._template_name)
        self._selected_important_columns = (
            set(stored)
            if stored is not None
            else set(important_csv_columns(columns, self._important_field_ids))
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
        self._sync_export_button()
        self.setWindowTitle(f"Visor de CSV — {path.name}")

    def _populate_table(self) -> None:
        """Llena la tabla por tramos para no bloquear la ventana.

        El primer tramo se escribe en el acto, así que un CSV corto queda
        completo al volver de aquí; solo las corridas grandes reparten el
        resto entre eventos de la interfaz.
        """
        self.table_sort.suspend()
        self.table.clear()
        self.table.setColumnCount(len(self._columns))
        self.table.setHorizontalHeaderLabels(self._columns)
        self.table.setRowCount(len(self._rows))
        self._pending_rows = list(range(len(self._rows)))
        self._fill_table_chunk()
        if self._pending_rows:
            self._table_timer.start()
        else:
            self._finish_table()

    def _rows_per_chunk(self) -> int:
        """Filas que caben en el presupuesto de celdas de un tramo."""
        return max(1, _TABLE_CELL_CHUNK // max(1, len(self._columns)))

    def _fill_table_chunk(self) -> None:
        """Escribe el siguiente tramo de filas pendientes."""
        batch = self._pending_rows[: self._rows_per_chunk()]
        del self._pending_rows[: len(batch)]
        self.table.setUpdatesEnabled(False)
        try:
            for row_index in batch:
                row = self._rows[row_index]
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

    def _on_table_chunk(self) -> None:
        if self._pending_rows:
            self._fill_table_chunk()
        if not self._pending_rows:
            self._table_timer.stop()
            self._finish_table()

    def _finish_table(self) -> None:
        """Cierra el llenado: columnas visibles, anchos y orden disponibles."""
        self._apply_column_mode()
        self.table.resizeColumnsToContents()
        self.table_sort.reset()

    def _load_pdf_paths(self, csv_path: Path) -> None:
        """Publica en el visor los PDF de los que proviene el CSV."""
        self._row_pdf_paths, documents, missing = resolve_source_documents(
            csv_path, self._rows, self._pdf_search_folders
        )
        self.pdf_viewer.load_paths(documents, missing)

    def _relocate_source_pdfs(self) -> None:
        """Reintenta la búsqueda de los PDF faltantes en otra carpeta."""
        selected = QFileDialog.getExistingDirectory(
            self,
            "Buscar los PDF de origen",
            str(self._folder or self._start_folder),
        )
        if not selected:
            return
        folder = Path(selected)
        if folder not in self._pdf_search_folders:
            self._pdf_search_folders.append(folder)
        csv_path = self._current_csv_path()
        if csv_path is not None:
            self._load_pdf_paths(csv_path)

    def _current_csv_path(self) -> Path | None:
        data = self.csv_combo.currentData()
        return Path(data) if data else None

    # ── Exportación ─────────────────────────────────────────────────────

    def _sync_export_button(self) -> None:
        """Solo se exporta la corrida que el CSV abierto permite rehacer."""
        csv_path = self._current_csv_path()
        if csv_path is None or not _companion_payload(csv_path).get("reportes"):
            reason = (
                "Este CSV no viene acompañado del JSON de su corrida, así que "
                "no se pueden volver a generar las salidas."
            )
        elif run_dir_for_csv(csv_path) is None:
            reason = (
                "Este CSV no está en la carpeta de su corrida, y las salidas "
                "solo se pueden volver a generar sobre ella."
            )
        else:
            reason = ""
        self.btn_export.setToolTip(reason or _EXPORT_TOOLTIP)
        self.btn_export.setEnabled(
            not reason
            and (self._outputs_worker is None
                 or not self._outputs_worker.isRunning())
        )

    def _important_columns_for_export(self, template) -> list[str]:
        """Columnas del CSV mínimo, independientes del dataset completo."""
        columns = CsvReporter.columns_for_fields(
            [field.id for field in template.fields],
            skip_ids=frozenset(
                field.id
                for field in template.fields
                if field.type.value == "signature"
            ),
        )
        return [
            column
            for column in columns
            if column in self._selected_important_columns
        ]

    def _exportar(self) -> None:
        """Regenera CSV, JSON y PDFs de la corrida abierta, sin repetir OCR."""
        if self._outputs_worker is not None and self._outputs_worker.isRunning():
            return
        csv_path = self._current_csv_path()
        if csv_path is None or run_dir_for_csv(csv_path) is None:
            return

        template = template_for_csv(csv_path)
        if template is None:
            QMessageBox.warning(
                self,
                "Plantilla no disponible",
                "No se encontró la plantilla con la que se procesó esta "
                "corrida, y sin ella no se pueden volver a generar las "
                "salidas.",
            )
            return
        try:
            reports, missing = reports_for_csv(csv_path, self._pdf_search_folders)
        except Exception as exc:  # noqa: BLE001 - se muestra en la GUI
            logger.error(f"No se pudo leer el JSON de la corrida: {exc}")
            QMessageBox.critical(
                self,
                "No se pudo exportar",
                f"El JSON de la corrida no se pudo leer:\n\n{exc}",
            )
            return
        if missing or not reports:
            QMessageBox.warning(
                self,
                "Faltan los PDF de origen",
                "No se encontraron los PDF de los que proviene esta corrida "
                f"({_join_names(missing) or 'ninguno disponible'}). Las "
                "páginas se rehacen desde ellos, así que indique dónde están "
                "con «Ubicar PDF…» antes de exportar.",
            )
            return

        from app.gui.worker import OutputsWorker

        worker = OutputsWorker(
            reports, self._export_options(csv_path, template, reports), parent=self
        )
        self._outputs_worker = worker
        worker.succeeded.connect(self._on_outputs_written)
        worker.failed.connect(self._on_outputs_failed)
        worker.progress.connect(self._on_outputs_stage)
        worker.finished.connect(self._on_outputs_finished)
        worker.finished.connect(worker.deleteLater)
        self.btn_export.setEnabled(False)
        self._export_note = "exportando salidas…"
        self._refresh_status()
        worker.start()

    def _export_options(self, csv_path: Path, template, reports: list):
        """Opciones de salida de la corrida abierta, escritas sobre su carpeta."""
        from app.core.config import AppConfig, config_for_pdf
        from app.reports.outputs import OutputOptions

        config = AppConfig()
        # La resolución se acota al escaneo, igual que al procesar: nunca se
        # interpola la página por encima del detalle que trae el PDF.
        dpi = config_for_pdf(config, Path(reports[0].pdf_path)).dpi
        return OutputOptions(
            template=template,
            output_root=_PROGRAM_DIR / "output",
            dpi=dpi,
            crop_padding=config.crop_padding,
            separar_por=tuple(self.export_options.separar_por() or ()),
            un_solo_pdf=self.export_options.radio_unico.isChecked(),
            discrepancias=self.export_options.discrepancias_check.isChecked(),
            errores=self.export_options.errores_check.isChecked(),
            debug=False,
            run_dir=run_dir_for_csv(csv_path),
            csv_date_mode=self.export_options.csv_date_mode(),
            important_csv_columns=tuple(
                self._important_columns_for_export(template)
            ),
        )

    def _on_outputs_stage(self, message: str, _percent: int) -> None:
        self._export_note = f"generando salidas… {message}"
        self._refresh_status()

    def _on_outputs_written(self, output_dir: Path) -> None:
        """Recarga el CSV: la exportación acaba de reescribirlo en disco."""
        logger.info(f"Exportación completada en: {output_dir}")
        csv_path = self._current_csv_path()
        if csv_path is not None and csv_path.is_file():
            self._load_csv(csv_path)
        self._export_note = f"exportación terminada: {Path(output_dir).name}"
        self._refresh_status()

    def _on_outputs_failed(self, message: str) -> None:
        logger.error(f"Error generando outputs: {message}")
        self._export_note = "error al exportar"
        self._refresh_status()
        QMessageBox.critical(
            self,
            "Error al exportar",
            message.splitlines()[0] if message else "Error desconocido",
        )

    def _on_outputs_finished(self) -> None:
        self._outputs_worker = None
        self._sync_export_button()

    def _on_current_cell_changed(
        self, row: int, _column: int, _previous_row: int, _previous_column: int
    ) -> None:
        """Sigue con el visor la fila activa de la tabla."""
        item = self.table.item(row, 0) if row >= 0 else None
        if item is None:
            return
        source_row = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(source_row, int) and 0 <= source_row < len(self._rows):
            self._show_row_in_pdf(source_row)

    def _show_row_in_pdf(self, source_row: int) -> str:
        """Muestra la página que originó la fila y describe su ubicación."""
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
            return f"{path.name}, página {page}"
        return f"{row.get('file', 'PDF desconocido')}, página {page or '?'}"

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

        location = self._show_row_in_pdf(source_row)
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
        self._important_fields_store.save(self._template_name, columns)
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
            sum(
                1
                for column in self._columns
                if column in self._selected_important_columns
            )
            if important_only
            else len(self._columns)
        )
        self._summary = (
            f"{len(self._rows)} fila(s) · {visible} de {len(self._columns)} "
            "columnas visibles · solo lectura"
        )
        self._refresh_status()

    def _refresh_status(self) -> None:
        """Resumen de la tabla, con el estado de la exportación al final."""
        note = f" · {self._export_note}" if self._export_note else ""
        self.status_label.setText(f"{self._summary}{note}")

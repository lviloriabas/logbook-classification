"""Ventana principal de Logbook Classification.

Permite seleccionar los archivos (PDFs), la plantilla, configurar el
procesamiento (motor OCR, páginas, corrección de inclinación, alineación…)
y las salidas (discrepancia, PDFs por matrícula/mes, visualizar campos),
con barra de progreso, tiempos estimados/transcurridos y tiempo por
archivo. Los resultados OCR se pueden exportar de nuevo con otra separación
sin volver a ejecutar el procesamiento.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from loguru import logger
from PySide6.QtCore import (
    QObject,
    QProcess,
    QRectF,
    QSize,
    Qt,
    QThread,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QImage,
    QIntValidator,
    QKeySequence,
    QPainter,
    QPen,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.core.config import AppConfig
from app.core.page_range import FileSlice, PageRange, slice_batch, total_pages
from app.core.parallelism import available_cpu_threads, recommended_parallelism
from app.gui.csv_utils import template_field_ids_for_columns
from app.gui.csv_viewer import (
    CsvColumnModeButton,
    CsvViewerWindow,
    ImportantFieldsButton,
    apply_csv_column_visibility,
)
from app.gui.eta import estimate_remaining_seconds, wall_ms_per_page
from app.gui.field_selector import ImportantFieldsDialog
from app.gui.fleet_editor import FLEET_FILENAME, FleetEditorDialog, FleetStore
from app.gui.table_sort import ColumnSortController
from app.gui.widgets import (
    DATA_TABLE_QSS,
    ZoomableScrollArea,
    load_zoom_icon,
    style_data_table,
)
from app.gui.worker import OutputsWorker, PipelineWorker, PreprocessWorker
from app.models.schemas import Status, ValidationReport
from app.reports.csv_reporter import (
    CSV_DATE_MONTH_END,
    CSV_DATE_SPECIFIC,
    CsvReporter,
)
from app.templates.manager import TemplateManager
from app.templates.schema import Template
from app.utils.important_fields import (
    IMPORTANT_FIELDS_FILENAME,
    ImportantFieldsStore,
)
from app.utils.io import send_to_trash
from app.validation.duplicates import DuplicateLogPage, detect_duplicate_log_pages

SCRIPT_DIR = Path(__file__).resolve().parents[2]
PERF_CACHE = SCRIPT_DIR / "output" / ".performance.json"
_DEFAULT_MS_PER_PAGE = 2500.0  # costo nominal antes de la primera corrida

_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
# Celdas —no filas— por tick del QTimer. El costo de llenar la tabla es por
# celda (medido: ~16 µs cada una), así que un presupuesto en filas escala con
# el número de columnas: 400 filas del CSV completo son 34.000 celdas, medio
# segundo de interfaz bloqueada por tick. Con un presupuesto en celdas cada
# tick cuesta lo mismo tenga la corrida 3 columnas o 90.
_TABLE_CELL_CHUNK = 2000
# Espera entre comprobaciones mientras se detiene el trabajo para cerrar.
_SHUTDOWN_POLL_MS = 150
_DUP_COLUMN = "dup"
_DISC_COLUMN = "disc"
# Alto de la consola y del panel de avance: caben seis archivos sin desplazar.
_BOTTOM_PANE_HEIGHT = 190
# Columna del nombre en el panel de avance; el resto de la fila es la barra.
_NAME_COLUMN_WIDTH = 220


_COLORS = {
    Status.OK: "#1a7f37",
    Status.WARNING: "#9a6700",
    Status.ERROR: "#cf222e",
}


def _visible_preview_fields(
    template: Template,
    important_only: bool,
    important_ids: set[str] | None = None,
):
    """Campos que debe pintar el visor.

    Sin ``important_only`` se dibuja la plantilla completa. Con la vista
    simplificada manda ``important_ids``: los campos marcados en el selector
    de campos importantes, incluidos los que la plantilla no declara
    ``required``. Solo cuando todavía no hay ninguna selección se recurre a
    ``required``, que es la importancia declarada por la propia plantilla.
    """
    if not important_only:
        return list(template.fields)
    if important_ids is None:
        return [field for field in template.fields if field.required]
    return [field for field in template.fields if field.id in important_ids]

_QSS = """
QMainWindow, QWidget {
    font-family: "Segoe UI", "Noto Sans", sans-serif;
    font-size: 10pt;
}
QWidget#previewContext, QLabel#previewContext {
    color: #57606a;
    font-weight: 600;
    padding: 4px 2px;
}
QPushButton {
    min-height: 26px;
    padding: 4px 12px;
}
#primaryButton {
    background-color: rgb(49, 49, 49);
    color: #ffffff;
}
#primaryButton:hover {
    background-color: rgb(64, 64, 64);
}
#primaryButton:pressed {
    background-color: rgb(38, 38, 38);
}
#zoomOverlay {
    background-color: rgb(49, 49, 49);
    border: 1px solid rgb(49, 49, 49);
    border-radius: 8px;
}
#zoomOverlay QLabel {
    border: 0;
    background: transparent;
    color: #ffffff;
    font-size: 10px;
    font-weight: 600;
}
#zoomOverlay QToolButton#zoomControl {
    min-width: 28px;
    max-width: 28px;
    min-height: 28px;
    max-height: 28px;
    padding: 0;
    border: 1px solid transparent;
    border-radius: 6px;
    background-color: rgb(49, 49, 49);
}
#zoomOverlay QToolButton#zoomControl:hover {
    background-color: rgb(64, 64, 64);
    border-color: rgb(102, 102, 102);
}
#zoomOverlay QToolButton#zoomControl:pressed {
    background-color: rgb(38, 38, 38);
    border-color: rgb(102, 102, 102);
}
#zoomOverlay QToolButton#zoomControl:disabled {
    background-color: rgb(49, 49, 49);
}
#zoomOverlay QLabel#zoomCaption,
#zoomOverlay QLabel#zoomValue {
    min-width: 28px;
    max-width: 28px;
    padding: 0;
    color: #ffffff;
    font-size: 10px;
    font-weight: 600;
}
QPushButton:disabled { color: #8c959f; }
QToolButton { padding: 2px 6px; }
QGroupBox {
    font-weight: 600;
    border: 1px solid #c9d1d9; border-radius: 6px;
    margin-top: 8px; padding: 8px 8px 6px 8px;
}
QGroupBox::title {
    subcontrol-origin: margin; left: 8px; padding: 0 4px;
}
QProgressBar {
    border: 1px solid #c9d1d9; border-radius: 5px;
    text-align: center;
}
QProgressBar::chunk { background-color: #2f81f7; border-radius: 4px; }
QSpinBox, QComboBox, QLineEdit { padding: 3px; }
#timeBar {
    background-color: #e1e7ee;
    font-size: 9pt; font-weight: 600; color: #24292f;
}
#filePages { color: #57606a; }
#timeSummary {
    background-color: rgb(49, 49, 49);
    border: 1px solid rgb(49, 49, 49);
    border-radius: 6px;
}
#timeSummary QLabel[role="caption"] {
    color: #ffffff;
    font-size: 10px;
}
#timeSummary QLabel[role="value"] {
    color: #ffffff;
    font-size: 10px;
    font-weight: 600;
}
""" + DATA_TABLE_QSS


def _format_duration(seconds: float) -> str:
    """Formatea una duración de forma limpia: s, min u horas y minutos."""
    seconds = max(0.0, seconds)
    if seconds < 60:
        return f"{round(seconds)} s"
    minutes = seconds / 60.0
    if minutes < 60:
        return f"{int(minutes)} min"
    hours = int(minutes // 60)
    mins = int(minutes % 60)
    return f"{hours} h {mins} min"


def _format_clock(seconds: float) -> str:
    """Muestra una duración con precisión de segundos, como un cronómetro."""
    total_seconds = max(0, int(round(seconds)))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _load_ms_per_page() -> float:
    """Costo por página (ms) aprendido de la última corrida."""
    try:
        import json

        with open(PERF_CACHE, encoding="utf-8") as fh:
            value = float(json.load(fh).get("ms_per_page", _DEFAULT_MS_PER_PAGE))
        return min(max(value, 100.0), 60000.0)
    except Exception:  # noqa: BLE001 - caché opcional
        return _DEFAULT_MS_PER_PAGE


def _save_ms_per_page(ms_per_page: float) -> None:
    try:
        import json

        PERF_CACHE.parent.mkdir(parents=True, exist_ok=True)
        with open(PERF_CACHE, "w", encoding="utf-8") as fh:
            json.dump({"ms_per_page": round(ms_per_page, 1)}, fh)
    except Exception as exc:  # noqa: BLE001 - no crítico
        logger.warning(f"No se pudo guardar el cálculo de rendimiento: {exc}")


def _clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()
        else:
            sub = item.layout()
            if sub is not None:
                _clear_layout(sub)


def _load_app_icon() -> QIcon:
    """Icono de la aplicación: .ico (multi-tamaño, ideal en Windows) o PNG."""
    assets = SCRIPT_DIR / "assets"
    for name in ("icon.ico", "icon.png"):
        path = assets / name
        if path.is_file():
            return QIcon(str(path))
    return QIcon()


class QtLogSink(QObject):
    """Sink de Loguru que reenvía mensajes a la GUI vía señal."""

    message = Signal(str)

    def __call__(self, msg) -> None:
        self.message.emit(str(msg))


class PreviewLoader(QObject):
    """Renderiza páginas de vista previa en su propia QThread.

    La GUI pide páginas con ``requested`` y recibe el resultado en
    ``previewReady`` sin bloquear el hilo de interfaz.
    """

    requested = Signal(int, str, object)
    previewReady = Signal(int, str, object)

    def run(
        self, page_number: int, pdf_path: str, geometry: dict | None = None
    ) -> None:
        import cv2

        from app.vision.alignment import TransformResult, apply_transform
        from app.vision.pdf_loader import render_page
        from app.vision.preprocessing import rotate

        try:
            image = render_page(Path(pdf_path), page_number, dpi=150)
            if geometry:
                skew_angle = float(geometry.get("skew_angle", 0.0))
                if abs(skew_angle) > 0.0:
                    image = rotate(image, skew_angle)
                alignment = geometry.get("alignment")
                if alignment:
                    height, width = image.shape[:2]
                    image = apply_transform(
                        image,
                        TransformResult(
                            rot=float(alignment.get("rot", 0.0)),
                            tx=float(alignment.get("tx_ratio", 0.0)) * width,
                            ty=float(alignment.get("ty_ratio", 0.0)) * height,
                            scale=float(alignment.get("scale", 1.0)),
                        ),
                    )
            rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb.shape
            qimage = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888).copy()
            self.previewReady.emit(page_number, pdf_path, qimage)
        except Exception as exc:  # noqa: BLE001 - vista previa no crítica
            logger.warning(
                f"Vista previa no disponible ({pdf_path} p. {page_number}): {exc}"
            )
            self.previewReady.emit(page_number, pdf_path, None)


class MainWindow(QMainWindow):
    """Ventana principal de la aplicación."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Logbook Classification")
        self.resize(1280, 800)
        self.setStyleSheet(_QSS)
        self.setWindowIcon(_load_app_icon())

        self._pdf_paths: list[Path] = []
        self._template_path: Path | None = None
        self._reports: list[ValidationReport] | None = None
        # Estadísticas de la corrida, copiadas al terminar: el worker se libera
        # en cuanto acaba y una re-exportación posterior las sigue necesitando.
        self._vlm_stats: list[dict] = []
        self._worker: PipelineWorker | None = None
        self._preprocess_worker: PreprocessWorker | None = None
        self._outputs_worker: OutputsWorker | None = None
        self._outputs_context: str | None = None
        self._corrida_dir: Path | None = None
        self._pending_export = False
        self._pending_csv_refresh = False
        self._preview_page = 1
        self._preview_total = 0
        self._preview_pdf: Path | None = None
        self._preview_documents: list[Path] = []
        self._preview_document_counts: list[int] = []
        self._preview_document_keys: list[str] = []
        self._row_pdfs: list[Path] = []
        self._preview_source_pixmap: QPixmap | None = None
        # Render sin recuadros: permite repintar el overlay al cambiar la
        # selección de campos sin volver a rasterizar la página.
        self._preview_base_image: QImage | None = None
        self._preview_zoom = 1.0  # 1.0 = ajustado a la altura disponible
        # Geometria de preprocesado por pagina (deskew + alineacion), no
        # la imagen: ver PreprocessWorker.
        self._preprocess_geometry: dict[tuple[str, int], dict] = {}
        self._preprocessed_active = False
        self._log_sink = QtLogSink()
        self._processed_template: Template | None = None
        self._processed_dpi: int | None = None
        self._last_run_cancelled = False

        self._detected_dpi = 200
        self._detected_dpis: dict[str, int] = {}
        # Páginas de cada PDF de la entrada, alineado con ``_pdf_paths``: es
        # lo que convierte el rango global del lote en tramos por archivo.
        self._input_page_counts: list[int] = []
        self._config: AppConfig | None = None
        self._ms_per_page = _load_ms_per_page()

        self._timer = QTimer(self)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._on_timer_tick)
        self._run_started: float | None = None
        self._done_global = 0
        self._total_global = 0
        self._last_done = 0
        self._spinner_idx = 0
        self._spinner_active = False

        self._log_buffer: list[str] = []
        self._log_timer = QTimer(self)
        self._log_timer.setInterval(300)
        self._log_timer.timeout.connect(self._flush_log_buffer)
        self._log_timer.start()

        self._table_timer = QTimer(self)
        # Intervalo cero: cada tramo se ejecuta cuando la cola de eventos queda
        # vacía, así que la ventana atiende clics y repintados entre uno y otro
        # en vez de acumular 120 ms de espera por tramo.
        self._table_timer.setInterval(0)
        self._table_timer.timeout.connect(self._on_table_chunk)
        self._table_columns: list[str] = []
        self._table_pending: list = []
        self._table_important_field_ids: set[str] = set()
        self._selected_important_columns: set[str] = set()
        self._important_fields_user_selected = False
        self._important_fields_store = ImportantFieldsStore(
            SCRIPT_DIR / IMPORTANT_FIELDS_FILENAME
        )
        self._csv_viewer: CsvViewerWindow | None = None

        self._preview_thread = QThread(self)
        self._preview_loader = PreviewLoader()
        self._preview_loader.moveToThread(self._preview_thread)
        self._preview_loader.requested.connect(self._preview_loader.run)
        self._preview_loader.previewReady.connect(self._on_preview_ready)
        self._preview_thread.start()
        self._preview_pending: tuple[int, str] | None = None
        self._preview_results: dict[tuple[str, int], object] = {}

        # Cierre ordenado: destruir un QThread en marcha aborta el proceso, así
        # que la ventana pide la parada y espera sin bloquear la interfaz.
        self._closing = False
        self._torn_down = False
        self._shutdown_timer = QTimer(self)
        self._shutdown_timer.setInterval(_SHUTDOWN_POLL_MS)
        self._shutdown_timer.timeout.connect(self._on_shutdown_tick)

        self._bottom_splitter_adjusted = False
        self._file_rows: dict[int, dict] = {}
        self._row_ms: dict[int, float] = {}
        self._row_started: dict[int, float] = {}
        self._current_file_index = 0
        self._file_page_counts: list[int] = []

        self._build_ui()
        # Si la aplicación termina sin pasar por el cierre de la ventana
        # (cierre de sesión, ``quit()`` desde otro sitio), los hilos se paran
        # igual: destruirlos en marcha aborta el proceso.
        application = QApplication.instance()
        if application is not None:
            application.aboutToQuit.connect(self._teardown)
        self._attach_logger()
        self._refresh_templates()
        self._restore_important_columns()
        self._install_zoom_shortcuts()

    def load_initial_data(self) -> None:
        """Carga los datos del disco después de mostrar la ventana."""
        self._load_default_input()

    # ── UI ──────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(5)

        controls = self._build_controls()
        root.addWidget(controls, 0)

        root.addLayout(self._build_progress_row())
        root.addWidget(self._build_splitter(), stretch=1)

        bottom = self._build_bottom_splitter()
        # Cuatro archivos visibles sin desplazar: por debajo de esto el panel
        # se queda en dos filas y deja de servir para seguir un lote.
        bottom.setMinimumHeight(150)
        root.addWidget(bottom)

    def _build_controls(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self._build_input_group())
        layout.addWidget(self._build_process_group())
        layout.addWidget(self._build_options_group())
        layout.addWidget(self._build_advanced_panel())
        return panel

    def _build_input_group(self) -> QGroupBox:
        group = QGroupBox("Entrada")
        grid = QGridLayout(group)
        grid.setContentsMargins(8, 5, 8, 5)
        grid.setColumnStretch(1, 1)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)

        grid.addWidget(QLabel("Archivos:"), 0, 0)
        self.input_edit = QLineEdit()
        self.input_edit.setReadOnly(True)
        self.input_edit.setPlaceholderText("Sin archivos seleccionados")
        self.input_edit.setToolTip("Archivos PDF que se van a procesar")
        self.input_edit.setAccessibleName("Archivos seleccionados")
        grid.addWidget(self.input_edit, 0, 1)

        btn_pick = QPushButton("Seleccionar archivos…")
        btn_pick.setToolTip("Elegir uno o varios PDF de cualquier carpeta")
        btn_pick.clicked.connect(self._browse_pdfs)
        grid.addWidget(btn_pick, 0, 2)

        btn_input = QPushButton("Detectar")
        btn_input.setToolTip("Detectar los PDF de la carpeta input/ del programa")
        btn_input.clicked.connect(self._load_default_input)
        grid.addWidget(btn_input, 0, 3)

        btn_clear_input = QPushButton("Vaciar input")
        btn_clear_input.setToolTip(
            "Mover todos los archivos de input/ a la Papelera de reciclaje"
        )
        btn_clear_input.clicked.connect(self._clear_input_folder)
        grid.addWidget(btn_clear_input, 0, 4)

        self.btn_clear_output = QPushButton("Vaciar output")
        self.btn_clear_output.setToolTip(
            "Mover todas las corridas de output/ a la Papelera de reciclaje"
        )
        self.btn_clear_output.clicked.connect(self._clear_output_folder)
        grid.addWidget(self.btn_clear_output, 0, 5)

        grid.addWidget(QLabel("Plantilla:"), 1, 0)
        self.template_combo = QComboBox()
        self.template_combo.setMinimumWidth(200)
        self.template_combo.currentIndexChanged.connect(
            self._refresh_preview_template
        )
        grid.addWidget(self.template_combo, 1, 1)

        btn_tpl = QPushButton("Buscar…")
        btn_tpl.setToolTip("Seleccionar una plantilla JSON personalizada")
        btn_tpl.clicked.connect(self._browse_template)
        grid.addWidget(btn_tpl, 1, 2)

        self.btn_editor = QPushButton("Abrir editor")
        self.btn_editor.setToolTip("Abrir el editor visual de plantillas")
        self.btn_editor.setAccessibleName("Abrir editor de plantillas")
        self.btn_editor.clicked.connect(self._open_template_editor)
        grid.addWidget(self.btn_editor, 1, 3)

        self.btn_csv_viewer = QPushButton("Visor de CSV…")
        self.btn_csv_viewer.setToolTip(
            "Abrir una ventana independiente para consultar una corrida procesada"
        )
        self.btn_csv_viewer.clicked.connect(self._open_csv_viewer)
        grid.addWidget(self.btn_csv_viewer, 1, 4)

        self.estimate_label = QLabel("")
        self.estimate_label.setStyleSheet("color: #667085;")
        self.estimate_label.setToolTip(
            "Estimación del tiempo total para procesar la entrada actual"
        )
        grid.addWidget(self.estimate_label, 2, 0, 1, 6)
        return group

    def _build_process_group(self) -> QGroupBox:
        group = QGroupBox("Procesamiento")
        row = QHBoxLayout(group)
        row.setContentsMargins(8, 5, 8, 5)
        row.setSpacing(8)

        engine_label = QLabel("OCR: Paddle v5 (CPU)")
        engine_label.setToolTip(
            "Motor y dispositivo CPU fijados internamente; no requiere "
            "configuración del usuario."
        )
        row.addWidget(engine_label)

        # El lote se numera de corrido, igual que lo navega el visor: un solo
        # rango decide qué páginas entran, caigan en los archivos que caigan.
        range_tip = (
            "Rango de páginas contando el lote entero de corrido, como en el "
            "visor: la página 1 es la primera del primer archivo y la "
            "numeración sigue en el siguiente sin reiniciarse. Arranca en la "
            "primera y la última página de todos los archivos seleccionados. "
            "Los archivos que quedan fuera del rango no se abren."
        )
        row.addWidget(QLabel("Páginas:"))
        self.page_from_spin = QSpinBox()
        self.page_from_spin.setRange(1, 1)
        self.page_from_spin.setValue(1)
        self.page_from_spin.setToolTip(range_tip)
        self.page_from_spin.setAccessibleName("Primera página del rango")
        self.page_from_spin.valueChanged.connect(self._on_page_from_changed)
        row.addWidget(self.page_from_spin)

        row.addWidget(QLabel("a"))
        self.page_to_spin = QSpinBox()
        # Ambos extremos son números de página reales: el final arranca en la
        # última del lote, para que se lea de un vistazo cuánto abarca.
        self.page_to_spin.setRange(1, 1)
        self.page_to_spin.setValue(1)
        self.page_to_spin.setToolTip(range_tip)
        self.page_to_spin.setAccessibleName("Última página del rango")
        self.page_to_spin.valueChanged.connect(self._on_page_to_changed)
        row.addWidget(self.page_to_spin)

        self.page_range_label = QLabel("")
        self.page_range_label.setStyleSheet("color: #667085;")
        self.page_range_label.setToolTip(range_tip)
        row.addWidget(self.page_range_label)

        self.deskew_check = QCheckBox("Corrección de inclinación")
        self.deskew_check.setChecked(True)
        self.deskew_check.setToolTip("Enderezar páginas inclinadas antes de alinear")
        row.addWidget(self.deskew_check)

        self.align_check = QCheckBox("Alineación")
        self.align_check.setChecked(True)
        self.align_check.setToolTip(
            "Alinear cada página contra la referencia de la plantilla"
        )
        row.addWidget(self.align_check)

        self.crop_preprocess_check = QCheckBox("Preprocesar recortes")
        self.crop_preprocess_check.setChecked(True)
        self.crop_preprocess_check.setToolTip(
            "Aplica localización de tinta y reescalado a cada recorte antes "
            "del OCR. Desactívelo para enviar los recortes crudos al motor."
        )
        row.addWidget(self.crop_preprocess_check)
        row.addStretch()
        return group

    def _build_options_group(self) -> QGroupBox:
        group = QGroupBox("Salidas")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(8, 5, 8, 5)
        layout.setSpacing(4)

        formato_row = QHBoxLayout()
        formato_row.setSpacing(10)
        formato_label = QLabel("Salida")
        formato_label.setStyleSheet("font-weight: 600;")
        formato_row.addWidget(formato_label)
        formato_row.addSpacing(8)
        self.modo_grupo = QButtonGroup(self)
        self.radio_varios = QRadioButton("Varios PDF")
        self.radio_varios.setToolTip("Genera un PDF por cada matrícula/mes marcado")
        self.radio_unico = QRadioButton("Un solo PDF")
        self.radio_unico.setToolTip(
            "Genera un único PDF con el mismo nombre que la carpeta de la "
            "corrida, con páginas separadoras de matrícula/mes para los "
            "criterios marcados"
        )
        self.modo_grupo.addButton(self.radio_varios)
        self.modo_grupo.addButton(self.radio_unico)
        self.radio_varios.setChecked(True)
        formato_row.addWidget(self.radio_varios)
        formato_row.addWidget(self.radio_unico)
        formato_row.addStretch()
        layout.addLayout(formato_row)

        sep_row = QHBoxLayout()
        sep_row.setSpacing(10)
        sep_label = QLabel("Separar")
        sep_label.setStyleSheet("font-weight: 600;")
        sep_row.addWidget(sep_label)
        sep_row.addSpacing(8)
        self.matricula_check = QCheckBox("Matrícula")
        self.matricula_check.setToolTip(
            "Varios PDF: un archivo por matrícula. "
            "Un solo PDF: página separadora por matrícula."
        )
        sep_row.addWidget(self.matricula_check)
        self.mes_check = QCheckBox("Mes")
        self.mes_check.setToolTip(
            "Varios PDF: un archivo por mes. Un solo PDF: página separadora por mes."
        )
        sep_row.addWidget(self.mes_check)

        self.discrepancias_check = QCheckBox("Discrepancias")
        self.discrepancias_check.setToolTip(
            "Un solo PDF: agrega al final una sección 'Posibles "
            "discrepancias'. Varios PDF: genera discrepancias.pdf."
        )
        sep_row.addWidget(self.discrepancias_check)
        sep_row.addStretch()
        layout.addLayout(sep_row)

        date_row = QHBoxLayout()
        date_row.setSpacing(10)
        date_label = QLabel("Fecha del CSV")
        date_label.setStyleSheet("font-weight: 600;")
        date_row.addWidget(date_label)
        date_row.addSpacing(8)
        self.csv_date_mode_combo = QComboBox()
        self.csv_date_mode_combo.addItem(
            "Día específico (si falta, fin de mes)", CSV_DATE_SPECIFIC
        )
        self.csv_date_mode_combo.addItem(
            "Último día del mes", CSV_DATE_MONTH_END
        )
        self.csv_date_mode_combo.setToolTip(
            "Cambia la fecha representada en el CSV sin volver a ejecutar OCR. "
            "El resultado OCR original se conserva."
        )
        self.csv_date_mode_combo.currentIndexChanged.connect(
            self._on_csv_date_mode_changed
        )
        date_row.addWidget(self.csv_date_mode_combo)
        date_row.addStretch()
        layout.addLayout(date_row)

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(divider)

        info_row = QHBoxLayout()
        info_label = QLabel("Opciones:")
        info_label.setStyleSheet("font-weight: 600;")
        info_row.addWidget(info_label)
        info_row.addSpacing(8)
        self.fields_check = QCheckBox("Visualizar campos")
        self.fields_check.setToolTip(
            "Mostrar los bounding boxes de los campos únicamente en la vista "
            "previa. Los PDFs exportados conservan las bitácoras sin marcas."
        )
        self.fields_check.toggled.connect(self._on_fields_toggled)
        info_row.addWidget(self.fields_check)
        self.important_fields_check = QCheckBox("Solo importantes")
        self.important_fields_check.setEnabled(False)
        self.important_fields_check.setToolTip(
            "Mostrar únicamente los campos marcados en la lista de campos "
            "importantes (botón contiguo). Sin la casilla se dibuja la "
            "plantilla completa."
        )
        self.important_fields_check.toggled.connect(self._on_fields_toggled)
        self.fields_check.toggled.connect(
            self.important_fields_check.setEnabled
        )
        info_row.addWidget(self.important_fields_check)
        self.important_fields_button = ImportantFieldsButton()
        self.important_fields_button.setToolTip(
            "Seleccionar los campos importantes; la lista se guarda por "
            "plantilla y decide qué recuadros y columnas se muestran."
        )
        self.important_fields_button.clicked.connect(self._open_important_fields)
        info_row.addWidget(self.important_fields_button)
        info_row.addStretch()
        layout.addLayout(info_row)

        fleet_row = QHBoxLayout()
        fleet_label = QLabel("Flota")
        fleet_label.setStyleSheet("font-weight: 600;")
        fleet_row.addWidget(fleet_label)
        self.fleet_check = QCheckBox("Verificar matrículas")
        self.fleet_check.setToolTip(
            "Marca para comparar las matrículas leídas contra fleet.json. "
            "Las matrículas ausentes se señalan para revisión."
        )
        fleet_row.addWidget(self.fleet_check)
        fleet_button = QPushButton("Editar lista…")
        fleet_button.setToolTip(
            f"Editar {FLEET_FILENAME}; manténgalo actualizado en la carpeta del programa."
        )
        fleet_button.clicked.connect(self._open_fleet_editor)
        fleet_row.addWidget(fleet_button)
        fleet_row.addWidget(
            QLabel(f"Archivo: {FLEET_FILENAME} en la carpeta del programa")
        )
        fleet_row.addStretch()
        layout.addLayout(fleet_row)
        return group

    def _build_advanced_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)

        self.advanced_btn = QToolButton()
        self.advanced_btn.setText("Opciones avanzadas")
        self.advanced_btn.setCheckable(True)
        self.advanced_btn.setArrowType(Qt.ArrowType.RightArrow)
        self.advanced_btn.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self.advanced_btn.toggled.connect(self._toggle_advanced)
        layout.addWidget(self.advanced_btn)

        self.advanced_panel = QWidget()
        self.advanced_panel.setVisible(False)
        adv = QVBoxLayout(self.advanced_panel)
        adv.setContentsMargins(0, 0, 0, 0)

        top_row = QHBoxLayout()
        available = available_cpu_threads()
        self._available_cpu_threads = available

        top_row.addWidget(QLabel("Hilos del procesador:"))
        self.threads_spin = QSpinBox()
        self.threads_spin.setRange(1, available)
        self.threads_spin.setValue(available)
        self.threads_spin.setToolTip(
            "Cantidad total de hilos que puede utilizar el procesamiento. "
            f"Disponibles detectados: {available}."
        )
        top_row.addWidget(self.threads_spin)

        top_row.addSpacing(12)
        top_row.addWidget(QLabel("Página de referencia:"))
        self.ref_spin = QSpinBox()
        self.ref_spin.setRange(1, 1000)
        self.ref_spin.setValue(1)
        self.ref_spin.setToolTip("Página usada como referencia de alineación")
        top_row.addWidget(self.ref_spin)

        top_row.addSpacing(16)
        self.reserve_core_check = QCheckBox(
            "Reservar un núcleo para la interfaz (recomendado)"
        )
        self.reserve_core_check.setChecked(True)
        self.reserve_core_check.setToolTip(
            "Deja un hilo del procesador libre para que la interfaz siga "
            "fluida mientras se procesa; el OCR usa los hilos restantes."
        )
        top_row.addWidget(self.reserve_core_check)
        top_row.addStretch()
        adv.addLayout(top_row)

        self.parallelism_hint = QLabel()
        self.parallelism_hint.setStyleSheet("color: #57606a;")
        adv.addWidget(self.parallelism_hint)
        self.threads_spin.valueChanged.connect(self._update_parallelism_hint)
        self.reserve_core_check.toggled.connect(self._update_parallelism_hint)
        self._update_parallelism_hint()

        check_row = QHBoxLayout()
        self.remove_printed_check = QCheckBox("Mapear fondo impreso (recomendado)")
        self.remove_printed_check.setChecked(True)
        self.remove_printed_check.setToolTip(
            "Construye un mapa de etiquetas, separadores y líneas de grilla "
            "idénticos en todas las páginas para firmas y ranuras de fecha. "
            "El OCR conserva la imagen original para no borrar escritura "
            "repetida."
        )
        check_row.addWidget(self.remove_printed_check)
        check_row.addStretch()
        adv.addLayout(check_row)

        date_info = QLabel(
            "OCR fijo en CPU: Paddle PP-OCRv5 mobile + detector v6 medium, "
            "sin cadena de motores de respaldo ni VLM."
        )
        date_info.setStyleSheet("color: #57606a;")
        adv.addWidget(date_info)

        layout.addWidget(self.advanced_panel)
        return panel

    def _effective_threads(self, selected: int) -> int:
        """Hilos efectivos del pipeline según la reserva para la interfaz."""
        checkbox = getattr(self, "reserve_core_check", None)
        if checkbox is not None and checkbox.isChecked() and selected > 1:
            return selected - 1
        return selected

    def _update_parallelism_hint(self) -> None:
        """Muestra la distribución automática para los hilos seleccionados.

        El reparto prioriza el número de procesos: un proceso extra rinde
        mucho más que un hilo interno extra (ver ``app/core/parallelism``),
        así que puede sobrar algún hilo sin que eso cueste velocidad.
        """
        selected_threads = self.threads_spin.value()
        effective = self._effective_threads(selected_threads)
        selected_workers, selected_per_worker = recommended_parallelism(effective)
        automatic = (
            f"{selected_workers} proceso(s) OCR x {selected_per_worker} "
            f"hilo(s)"
        )
        if self._effective_threads(selected_threads) < selected_threads:
            automatic += (
                f", {selected_threads - effective} hilo(s) reservado(s) "
                "para la interfaz"
            )
        if selected_threads == self._available_cpu_threads:
            current = " Es la configuración más rápida y está seleccionada por defecto."
        else:
            current = (
                f" La configuración más rápida usa los "
                f"{self._available_cpu_threads} hilos disponibles."
            )
        self.parallelism_hint.setText(f"Distribución automática: {automatic}.{current}")

    def _toggle_advanced(self, checked: bool) -> None:
        self.advanced_panel.setVisible(checked)
        self.advanced_btn.setArrowType(
            Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow
        )
        if checked and self.height() < self.minimumSizeHint().height():
            self.resize(self.width(), self.minimumSizeHint().height())

    def _build_progress_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)

        self.status_label = QLabel("Listo.")
        row.addWidget(self.status_label, 1)

        self.busy_label = QLabel("")
        self.busy_label.setMinimumWidth(24)
        self.busy_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.busy_label.setToolTip("Procesamiento en curso")
        row.addWidget(self.busy_label)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        row.addWidget(self.progress, 2)

        time_summary = QFrame()
        time_summary.setObjectName("timeSummary")
        time_summary.setMinimumWidth(270)
        time_summary.setFixedHeight(30)
        time_summary.setToolTip(
            "El tiempo restante se recalcula con las páginas completadas y el "
            "ritmo observado."
        )
        time_layout = QHBoxLayout(time_summary)
        time_layout.setContentsMargins(9, 2, 9, 2)
        time_layout.setSpacing(12)
        self.time_labels: dict[str, QLabel] = {}
        for key, caption in (
            ("elapsed", "TRANSCURRIDO"),
            ("remaining", "RESTANTE"),
            ("total", "ESTIMADO"),
        ):
            metric = QVBoxLayout()
            metric.setSpacing(1)
            caption_label = QLabel(caption)
            caption_label.setProperty("role", "caption")
            value_label = QLabel("--:--:--")
            value_label.setProperty("role", "value")
            value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            metric.addWidget(caption_label, alignment=Qt.AlignmentFlag.AlignCenter)
            metric.addWidget(value_label)
            time_layout.addLayout(metric, 1)
            self.time_labels[key] = value_label
        row.addWidget(time_summary)

        self.btn_process = QPushButton("Procesar")
        self.btn_process.setObjectName("primaryButton")
        self.btn_process.setDefault(True)
        self.btn_process.clicked.connect(self._start_processing)

        self.btn_preprocess = QPushButton("Preprocesar")
        self.btn_preprocess.setToolTip(
            "Aplica corrección de inclinación y alineación sin ejecutar OCR."
        )
        self.btn_preprocess.clicked.connect(self._start_preprocessing)
        row.addWidget(self.btn_preprocess)
        row.addWidget(self.btn_process)

        self.btn_cancel = QPushButton("Cancelar")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.setToolTip(
            "Detener el procesamiento; las páginas ya leídas se guardan en el CSV"
        )
        self.btn_cancel.clicked.connect(self._request_cancel)
        row.addWidget(self.btn_cancel)

        self.btn_export = QPushButton("Exportar")
        self.btn_export.setEnabled(False)
        self.btn_export.setToolTip(
            "Volver a generar CSV, JSON y PDFs con las opciones actuales, "
            "sin reprocesar los archivos. Los PDFs ya exportados se "
            "conservan: los nuevos se numeran (-2, -3…) si el nombre se "
            "repite"
        )
        self.btn_export.clicked.connect(self._exportar)
        row.addWidget(self.btn_export)
        return row

    def _set_time_summary(
        self,
        elapsed: float | None = None,
        remaining: float | None = None,
        total: float | None = None,
    ) -> None:
        """Actualiza las tres métricas sin mezclar estados o estimaciones."""
        values = {
            "elapsed": elapsed,
            "remaining": remaining,
            "total": total,
        }
        for key, value in values.items():
            self.time_labels[key].setText(
                _format_clock(value) if value is not None else "--:--:--"
            )

    def _build_splitter(self) -> QSplitter:
        splitter = QSplitter(Qt.Orientation.Horizontal)

        preview_widget = QWidget()
        preview_layout = QVBoxLayout(preview_widget)
        preview_layout.setContentsMargins(0, 0, 0, 0)

        self.preview_label = QLabel("Vista previa")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumSize(0, 0)
        self.preview_label.setStyleSheet(
            "border: 1px solid #bbb; background: transparent;"
        )
        self.preview_label.setAccessibleName("Vista previa de la página")

        self.preview_scroll = ZoomableScrollArea()
        self.preview_scroll.set_zoom_callback(self._zoom_preview)
        self.preview_scroll.setWidgetResizable(False)
        self.preview_scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_scroll.setMinimumSize(300, 220)
        self.preview_scroll.setWidget(self.preview_label)

        self.preview_pagination = QWidget()
        self.preview_pagination.setObjectName("previewPagination")
        nav = QHBoxLayout(self.preview_pagination)
        nav.setContentsMargins(0, 0, 0, 0)
        self.btn_prev = QToolButton()
        self.btn_prev.setArrowType(Qt.ArrowType.LeftArrow)
        self.btn_prev.setToolTip("Página anterior (flecha izquierda)")
        self.btn_prev.setAccessibleName("Página anterior")
        self.btn_prev.setShortcut(QKeySequence(Qt.Key.Key_Left))
        self.btn_prev.setEnabled(False)
        self.btn_prev.clicked.connect(self._prev_page)
        self.btn_next = QToolButton()
        self.btn_next.setArrowType(Qt.ArrowType.RightArrow)
        self.btn_next.setToolTip("Página siguiente (flecha derecha)")
        self.btn_next.setAccessibleName("Página siguiente")
        self.btn_next.setShortcut(QKeySequence(Qt.Key.Key_Right))
        self.btn_next.setEnabled(False)
        self.btn_next.clicked.connect(self._next_page)
        self.preview_file_label = QLabel("Ninguno")
        self.preview_file_label.setMaximumWidth(320)
        self.preview_file_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.preview_file_label.setAccessibleName(
            "Archivo PDF activo en la vista previa"
        )
        self.preview_file_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.page_edit = QLineEdit()
        self.page_edit.setValidator(QIntValidator(1, 1, self.page_edit))
        self.page_edit.setToolTip(
            "Escriba el número de página del lote; el salto se aplica al terminar"
        )
        self.page_edit.setAccessibleName("Página actual")
        self.page_edit.setFixedWidth(48)
        self.page_edit.editingFinished.connect(self._jump_to_page_number)
        self.page_total_label = QLabel("de 0")
        nav.addWidget(self.btn_prev)
        nav.addWidget(QLabel("Página"))
        nav.addWidget(self.page_edit)
        nav.addWidget(self.page_total_label)
        nav.addWidget(self.btn_next)

        self.preview_file_indicator = QWidget()
        file_row = QHBoxLayout(self.preview_file_indicator)
        file_row.setContentsMargins(0, 0, 10, 0)
        file_row.setSpacing(4)
        file_row.addWidget(QLabel("Archivo:"))
        file_row.addWidget(self.preview_file_label)

        # Una sola barra: el archivo queda anclado a la izquierda y el
        # paginador se centra sobre todo el ancho disponible del PDF.
        self.preview_nav_bar = QWidget()
        self.preview_nav_bar.setObjectName("previewNavigationBar")
        nav_bar = QGridLayout(self.preview_nav_bar)
        nav_bar.setContentsMargins(0, 0, 0, 0)
        nav_bar.addWidget(
            self.preview_file_indicator,
            0,
            0,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        )
        nav_bar.addWidget(
            self.preview_pagination,
            0,
            0,
            Qt.AlignmentFlag.AlignCenter,
        )

        viewer_frame = QWidget()
        viewer_frame_layout = QGridLayout(viewer_frame)
        viewer_frame_layout.setContentsMargins(0, 0, 0, 0)
        viewer_frame_layout.addWidget(self.preview_scroll, 0, 0)

        zoom_overlay = QFrame(viewer_frame)
        zoom_overlay.setObjectName("zoomOverlay")
        zoom_overlay.setFixedWidth(42)
        zoom_panel = QVBoxLayout(zoom_overlay)
        zoom_panel.setContentsMargins(5, 6, 5, 6)
        zoom_panel.setSpacing(2)
        zoom_title = QLabel("Zoom")
        zoom_title.setObjectName("zoomCaption")
        zoom_title.setFixedWidth(28)
        zoom_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        zoom_panel.addWidget(zoom_title, 0, Qt.AlignmentFlag.AlignHCenter)
        self.btn_zoom_in = QToolButton()
        self.btn_zoom_in.setObjectName("zoomControl")
        self.btn_zoom_in.setIcon(load_zoom_icon("in"))
        self.btn_zoom_in.setToolTip("Acercar la vista previa")
        self.btn_zoom_in.setAccessibleName("Acercar vista previa")
        self.btn_zoom_in.setIconSize(QSize(14, 14))
        self.btn_zoom_in.setFixedSize(28, 28)
        self.btn_zoom_in.clicked.connect(lambda: self._zoom_preview(1.25))
        zoom_panel.addWidget(self.btn_zoom_in, 0, Qt.AlignmentFlag.AlignHCenter)
        self.btn_zoom_fit = QToolButton()
        self.btn_zoom_fit.setObjectName("zoomControl")
        self.btn_zoom_fit.setIcon(load_zoom_icon("fit"))
        self.btn_zoom_fit.setToolTip(
            "Ajustar la vista previa a la altura de la ventana"
        )
        self.btn_zoom_fit.setAccessibleName("Ajustar página a la ventana")
        self.btn_zoom_fit.setIconSize(QSize(14, 14))
        self.btn_zoom_fit.setFixedSize(28, 28)
        self.btn_zoom_fit.clicked.connect(self._fit_preview_vertical)
        zoom_panel.addWidget(self.btn_zoom_fit, 0, Qt.AlignmentFlag.AlignHCenter)
        self.btn_zoom_out = QToolButton()
        self.btn_zoom_out.setObjectName("zoomControl")
        self.btn_zoom_out.setIcon(load_zoom_icon("out"))
        self.btn_zoom_out.setToolTip("Alejar la vista previa")
        self.btn_zoom_out.setAccessibleName("Alejar vista previa")
        self.btn_zoom_out.setIconSize(QSize(14, 14))
        self.btn_zoom_out.setFixedSize(28, 28)
        self.btn_zoom_out.clicked.connect(lambda: self._zoom_preview(0.8))
        zoom_panel.addWidget(self.btn_zoom_out, 0, Qt.AlignmentFlag.AlignHCenter)
        self.zoom_label = QLabel("100%")
        self.zoom_label.setObjectName("zoomValue")
        self.zoom_label.setFixedWidth(28)
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        zoom_panel.addWidget(self.zoom_label, 0, Qt.AlignmentFlag.AlignHCenter)

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

        page_area = QWidget()
        page_layout = QVBoxLayout(page_area)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.addWidget(viewer_frame, stretch=1)
        page_layout.addWidget(self.preview_nav_bar)
        preview_layout.addWidget(page_area, stretch=1)
        self._update_preview_zoom_controls()

        self.table = QTableWidget(0, 0)
        self.table.setAccessibleName("Resultados de validación")
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        style_data_table(self.table)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionsClickable(True)
        self.table.horizontalHeader().setHighlightSections(False)
        self.table.horizontalHeader().setSectionsMovable(False)
        self.table.horizontalHeader().setFixedHeight(30)
        self.table_sort = ColumnSortController(self.table)
        self.table.cellDoubleClicked.connect(self._jump_to_page)

        table_panel = QWidget()
        table_layout = QVBoxLayout(table_panel)
        table_layout.setContentsMargins(0, 0, 0, 0)
        table_layout.addWidget(self.table, 1)
        table_controls = QHBoxLayout()
        self.duplicates_label = QLabel("Duplicados: 0")
        self.duplicates_label.setAccessibleName("Resumen de duplicados")
        self.duplicates_label.setToolTip(
            "No hay log_number repetidos en el lote procesado."
        )
        self.duplicates_label.setStyleSheet("color: #57606a;")
        table_controls.addWidget(self.duplicates_label)
        table_controls.addStretch()
        self.csv_columns_toggle = CsvColumnModeButton()
        self.csv_columns_toggle.setEnabled(False)
        self.csv_columns_toggle.setVisible(False)
        self.csv_columns_toggle.toggled.connect(self._apply_csv_table_view)
        table_controls.addWidget(self.csv_columns_toggle)
        table_layout.addLayout(table_controls)

        splitter.addWidget(preview_widget)
        splitter.addWidget(table_panel)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        return splitter

    def _build_bottom_splitter(self) -> QSplitter:
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setAccessibleName("Registro de eventos")
        self.log_view.setMaximumHeight(_BOTTOM_PANE_HEIGHT)
        self.log_view.document().setMaximumBlockCount(2000)
        self.log_view.setMinimumWidth(340)
        splitter.addWidget(self.log_view)

        times = QWidget()
        times_layout = QVBoxLayout(times)
        times_layout.setSpacing(4)
        title = QLabel("Avance por archivo")
        title.setStyleSheet("font-weight: bold;")
        times_layout.addWidget(title)

        self.preview_context_label = QLabel(
            "Archivo 0 de 0 · Página 0 de 0 en el archivo"
        )
        self.preview_context_label.setObjectName("previewContext")
        self.preview_context_label.setWordWrap(True)
        times_layout.addWidget(self.preview_context_label)

        self.times_vbox = QVBoxLayout()
        self.times_vbox.setContentsMargins(0, 0, 0, 0)
        self.times_vbox.setSpacing(3)
        self.times_container = QWidget()
        self.times_container.setLayout(self.times_vbox)

        self.times_scroll = QScrollArea()
        self.times_scroll.setWidgetResizable(True)
        self.times_scroll.setWidget(self.times_container)
        self.times_scroll.setMaximumHeight(_BOTTOM_PANE_HEIGHT)
        times_layout.addWidget(self.times_scroll, 1)

        self.empty_times_label = QLabel("Sin archivos procesados aún.")
        times_layout.addWidget(self.empty_times_label)

        # Mitad y mitad, como la tabla y el visor del Visor de CSV: el panel
        # lleva cuatro columnas por archivo y la consola no necesita el resto.
        # El mínimo es la fila completa, para que nunca haya scroll lateral.
        times.setMinimumWidth(560)
        splitter.addWidget(times)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.splitterMoved.connect(self._on_bottom_splitter_moved)
        self.bottom_splitter = splitter
        return splitter

    def _on_bottom_splitter_moved(self, _position: int, _index: int) -> None:
        """Una vez que el usuario reparte el espacio, se respeta su medida."""
        self._bottom_splitter_adjusted = True

    def _balance_bottom_splitter(self) -> None:
        """Reparte el ancho entre consola y panel mientras nadie lo ajuste."""
        if self._bottom_splitter_adjusted:
            return
        available = max(
            0, self.bottom_splitter.width() - self.bottom_splitter.handleWidth()
        )
        half = available // 2
        self.bottom_splitter.setSizes([half, available - half])

    def _make_file_row(self) -> dict:
        """Fila del panel: nombre, barra con porcentaje, páginas y reloj."""
        row = QHBoxLayout()
        row.setSpacing(8)
        name = QLabel("")
        # Anchos fijos en las tres columnas de texto: con anchos mínimos cada
        # fila empezaba la barra en un sitio distinto según lo largo que
        # fuera el nombre, y el panel dejaba de leerse como una tabla.
        name.setFixedWidth(_NAME_COLUMN_WIDTH)
        bar = QProgressBar()
        bar.setObjectName("timeBar")
        # La barra de 14 px recortaba el texto y el porcentaje no llegaba a
        # verse; con la altura de una línea cabe dentro de la propia barra.
        bar.setFixedHeight(20)
        bar.setMinimumWidth(140)
        bar.setRange(0, 100)
        bar.setValue(0)
        bar.setTextVisible(True)
        # RAE: espacio entre la cifra y el signo. La hoja ya lo centra.
        bar.setFormat("%p %")
        pages = QLabel("–")
        pages.setObjectName("filePages")
        pages.setFixedWidth(86)
        pages.setAlignment(Qt.AlignmentFlag.AlignRight
                           | Qt.AlignmentFlag.AlignVCenter)
        pages.setToolTip("Páginas procesadas de las que tiene el archivo")
        secs = QLabel("–")
        secs.setFixedWidth(70)
        secs.setAlignment(Qt.AlignmentFlag.AlignRight
                          | Qt.AlignmentFlag.AlignVCenter)
        secs.setToolTip("Tiempo que lleva el archivo")
        row.addWidget(name)
        row.addWidget(bar, 1)
        row.addWidget(pages)
        row.addWidget(secs)
        self.times_vbox.addLayout(row)
        return {"name": name, "bar": bar, "pages": pages, "secs": secs}

    @staticmethod
    def _set_row_name(row: dict, name: str, tooltip: str = "") -> None:
        """Escribe el nombre recortado a la columna, con el completo al pasar."""
        label = row["name"]
        label.setToolTip(tooltip or name)
        label.setText(
            label.fontMetrics().elidedText(
                name, Qt.TextElideMode.ElideMiddle, _NAME_COLUMN_WIDTH - 2
            )
        )

    @staticmethod
    def _set_row_pages(row: dict, done: int, total: int) -> None:
        """Escribe el contador de páginas y la barra de la fila."""
        row["pages"].setText(f"{done}/{total} pág." if total else f"{done} pág.")
        row["bar"].setRange(0, 100)
        row["bar"].setValue(round(done * 100 / total) if total else 0)

    def _prepare_file_rows(self, paths: list[Path]) -> None:
        """Lista el lote completo antes de empezar, con sus páginas y 0 %.

        El planificador puede arrancar varios archivos a la vez, así que las
        filas no pueden aparecer a medida que cada uno empieza: la lista se
        muestra entera desde el principio y cada fila avanza por su cuenta.
        """
        self._clear_times()
        if not paths:
            return
        self.empty_times_label.setVisible(False)
        for index, path in enumerate(paths):
            row = self._make_file_row()
            self._set_row_name(row, path.name, str(path))
            self._set_row_pages(row, 0, self._pages_of_file(index))
            self._file_rows[index] = row
        self.times_vbox.addStretch()

    def _pages_of_file(self, index: int) -> int:
        """Páginas previstas del archivo ``index`` (0-based), 0 si no se sabe."""
        if 0 <= index < len(self._file_page_counts):
            return self._file_page_counts[index]
        return 0

    def _ensure_file_rows(self, total: int) -> None:
        """Crea una fila por archivo en el panel de tiempos (idempotente)."""
        if total == 0 or len(self._file_rows) == total:
            return
        _clear_layout(self.times_vbox)
        self._file_rows = {}
        self._row_ms = {}
        self._row_started = {}
        for index in range(total):
            self._file_rows[index] = self._make_file_row()
            self._set_row_pages(
                self._file_rows[index], 0, self._pages_of_file(index)
            )
        self.times_vbox.addStretch()

    def _set_file_page_counts(self, slices: list[FileSlice]) -> None:
        """Páginas previstas de cada archivo de la corrida, ya recortadas."""
        self._file_page_counts = [item.count or 0 for item in slices]

    def _on_file_progress(self, index: int, done: int, total: int) -> None:
        """Avance real del archivo ``index`` (1-based), venga de donde venga.

        El planificador reparte páginas de un archivo o archivos completos
        según el tamaño del lote, y antes solo la primera estrategia movía
        las barras: la global se repartía en orden de archivo, así que con
        varios PDF en vuelo se llenaban los de arriba mientras avanzaban
        otros. Con el avance por archivo las dos estrategias se ven igual.
        """
        row = self._file_rows.get(index - 1)
        if row is None:
            return
        self._set_row_pages(row, done, total or self._pages_of_file(index - 1))

    # ── Logging ─────────────────────────────────────────────────────────

    def _attach_logger(self) -> None:
        from loguru import logger as lg

        lg.add(
            self._log_sink,
            level="INFO",
            format="{time:HH:mm:ss} | {level: <8} | {message}",
            enqueue=True,
        )
        self._log_sink.message.connect(self._on_log_message)
        logger.info("GUI iniciada")

    def _on_log_message(self, message: str) -> None:
        """Acumula líneas de log y las descarga por lotes: la GUI nunca
        procesa una señal Qt por mensaje ni un append por línea."""
        line = str(message).strip()
        if not line:
            return
        self._log_buffer.append(line)
        if len(self._log_buffer) >= 500:
            self._flush_log_buffer()

    def _flush_log_buffer(self) -> None:
        if not self._log_buffer:
            return
        batch = self._log_buffer
        self._log_buffer = []
        self.log_view.setUpdatesEnabled(False)
        try:
            for line in batch:
                self.log_view.append(line)
        finally:
            self.log_view.setUpdatesEnabled(True)
            scrollbar = self.log_view.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())

    # ── Entrada y estimación ────────────────────────────────────────────

    def _load_default_input(self) -> None:
        folder = SCRIPT_DIR / "input"
        if not folder.is_dir():
            self._set_input_paths([])
            return
        pdfs = sorted(
            p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".pdf"
        )
        self._set_input_paths(pdfs)
        if pdfs:
            logger.info(f"Entrada por defecto: {len(pdfs)} archivo(s) de {folder}")

    def _set_input_paths(self, paths: list[Path]) -> None:
        self._pdf_paths = []
        self._preprocess_geometry = {}
        self._preprocessed_active = False
        seen: set[str] = set()
        for p in paths:
            p = Path(p)
            key = str(p.resolve())
            if p.exists() and p.suffix.lower() == ".pdf" and key not in seen:
                seen.add(key)
                self._pdf_paths.append(p)
        self._refresh_input_summary()
        # El DPI se detecta primero porque esa pasada ya cuenta las páginas de
        # cada PDF: pasárselas al visor evita abrir el lote entero por segunda
        # vez, que era la mitad del tiempo que la ventana pasaba congelada al
        # elegir archivos (medido: 65 ms por PDF, la mitad en cada apertura).
        self._detect_dpi()
        self._set_preview_documents(self._pdf_paths, self._input_page_counts)
        self._preview_selected_input()
        self._refresh_estimate()

    def _preview_selected_input(self) -> None:
        """Muestra la entrada seleccionada antes de iniciar el procesamiento.

        El render se realiza en ``PreviewLoader`` para que seleccionar PDFs no
        espere al OCR ni bloquee la interfaz. La misma vista se sustituye por
        la página solicitada por el resultado del procesamiento cuando este
        termina.
        """
        if not self._pdf_paths:
            self._preview_pending = None
            self._preview_pdf = None
            self._preview_page = 1
            self._preview_total = 0
            self._preview_source_pixmap = None
            self._preview_base_image = None
            self._set_preview_documents([])
            self._preview_zoom = 1.0
            self.preview_label.clear()
            self.preview_label.setText("Vista previa")
            self._update_preview_zoom_controls()
            self._update_preview_nav()
            return
        self._preview_source_pixmap = None
        self._preview_base_image = None
        self._preview_zoom = 1.0
        self.preview_label.clear()
        self.preview_label.setText("Cargando vista previa…")
        self._update_preview_zoom_controls()
        self._show_preview_page(1, self._pdf_paths[0])

    @staticmethod
    def _document_key(path: Path) -> str:
        """Identidad de un PDF para comparar documentos entre sí."""
        return str(Path(path).resolve()).casefold()

    def _set_preview_documents(
        self, paths: list[Path], counts: list[int] | None = None
    ) -> None:
        """Actualiza la secuencia global de PDFs sin perder el activo.

        ``counts`` permite reutilizar un recuento de páginas ya hecho (el de
        la detección de DPI) en vez de reabrir cada PDF solo para contarlas.
        """
        from app.vision.pdf_loader import page_count

        known: dict[str, int] = {}
        if counts is not None and len(counts) == len(paths):
            known = {str(Path(path)): count for path, count in zip(paths, counts)}

        unique: list[Path] = []
        document_counts: list[int] = []
        keys: list[str] = []
        seen: set[str] = set()
        for path in paths:
            path = Path(path)
            key = self._document_key(path)
            if path.is_file() and path.suffix.lower() == ".pdf" and key not in seen:
                seen.add(key)
                unique.append(path)
                keys.append(key)
                count = known.get(str(path))
                if count is None:
                    try:
                        count = max(0, page_count(path))
                    except Exception:  # noqa: BLE001 - el visor omite PDFs inválidos
                        count = 0
                document_counts.append(max(0, count))
        self._preview_documents = unique
        self._preview_document_counts = document_counts
        # Las claves se calculan una vez: ``resolve()`` toca el disco y la
        # navegación las consultaba una vez por documento en cada página.
        self._preview_document_keys = keys
        active = (
            self._document_key(self._preview_pdf)
            if self._preview_pdf is not None
            else ""
        )
        if active and active not in seen:
            self._preview_pdf = None
            self._preview_page = 1
            self._preview_total = 0
        self._update_preview_nav()

    def _preview_document_index(self) -> int:
        """Posición del PDF activo dentro de la secuencia, o ``-1``."""
        if self._preview_pdf is None:
            return -1
        current = self._document_key(self._preview_pdf)
        try:
            return self._preview_document_keys.index(current)
        except ValueError:
            return -1

    def _preview_global_page(self) -> int:
        """Posición de la página actual dentro de todos los PDFs."""
        index = self._preview_document_index()
        if index < 0:
            return 0
        count = self._preview_document_counts[index]
        if count <= 0:
            return 0
        offset = sum(self._preview_document_counts[:index])
        return offset + min(max(1, self._preview_page), count)

    def _preview_location(self, global_page: int) -> tuple[Path, int] | None:
        """Convierte una página global en (PDF, página local)."""
        total = sum(self._preview_document_counts)
        if total <= 0:
            return None
        remaining = min(max(1, global_page), total)
        for path, count in zip(
            self._preview_documents, self._preview_document_counts
        ):
            if remaining <= count:
                return path, remaining
            remaining -= count
        return None

    def _jump_to_page_number(self) -> None:
        if self._preview_pdf is None:
            return
        try:
            global_page = int(self.page_edit.text())
        except ValueError:
            self.page_edit.setText(str(self._preview_global_page()))
            self.page_edit.setModified(False)
            return
        location = self._preview_location(global_page)
        if location is None:
            return
        self.page_edit.setModified(False)
        self._show_preview_page(location[1], location[0])

    def _open_fleet_editor(self) -> None:
        dialog = FleetEditorDialog(FleetStore(SCRIPT_DIR / FLEET_FILENAME), self)
        if dialog.exec():
            logger.info(f"Lista de flota actualizada: {SCRIPT_DIR / FLEET_FILENAME}")

    def _open_important_fields(self) -> None:
        columns = self._table_columns or self._columns_for_template_preview()
        if not columns:
            QMessageBox.information(
                self,
                "Campos importantes",
                "Cargue o procese un CSV para seleccionar sus columnas.",
            )
            return
        dialog = ImportantFieldsDialog(
            columns, self._current_important_columns(columns), self
        )
        dialog.selectionChanged.connect(self._set_important_columns)
        dialog.exec()

    def _set_important_columns(self, columns: set[str]) -> None:
        """Aplica y recuerda la selección hecha en el selector."""
        self._important_fields_user_selected = True
        self._selected_important_columns = set(columns)
        self._important_fields_store.save(self._template_key(), columns)
        self._apply_csv_table_view()
        self._apply_preview_overlay()

    def _template_key(self) -> str | None:
        """Nombre de la plantilla bajo el que se recuerda la selección."""
        template = self._processed_template or self._load_template()
        return template.name if template is not None else None

    def _current_important_columns(
        self, columns: list[str] | None = None
    ) -> set[str]:
        """Columnas marcadas, o el conjunto por defecto si nunca se editó."""
        if self._important_fields_user_selected:
            return set(self._selected_important_columns)
        if columns is None:
            columns = self._table_columns or self._columns_for_template_preview()
        return self._default_important_columns(columns)

    def _restore_important_columns(self) -> None:
        """Recupera del disco la selección guardada para la plantilla activa."""
        stored = self._important_fields_store.load(self._template_key())
        self._important_fields_user_selected = stored is not None
        self._selected_important_columns = (
            set(stored) if stored is not None else self._current_important_columns()
        )
        self._apply_csv_table_view()

    def _current_important_field_ids(self, template: Template) -> set[str] | None:
        """Campos de la plantilla que corresponden a las columnas marcadas."""
        columns = self._table_columns or self._columns_for_template_preview(template)
        if not columns:
            return None  # sin columnas conocidas manda ``required``
        return template_field_ids_for_columns(
            self._current_important_columns(columns),
            [field.id for field in template.fields],
            columns,
        )

    def _columns_for_template_preview(
        self, template: Template | None = None
    ) -> list[str]:
        template = template or self._processed_template or self._load_template()
        if template is None:
            return []
        signature_ids = {
            field.id for field in template.fields if field.type.value == "signature"
        }
        return CsvReporter.columns_for_fields(
            [field.id for field in template.fields],
            skip_ids=frozenset(signature_ids),
        )

    def _default_important_columns(self, columns: list[str]) -> set[str]:
        """Incluye los identificadores y campos críticos disponibles."""
        important = {
            "file", "page", "date", "time_ms", "dup", "disc", "log_number",
            "matricula", "flight_number", "pilot_signature",
            "captain_signature", "captain_license",
        }
        important.update(column for column in columns if column.endswith("_signature"))
        return set(columns).intersection(important)

    def _detect_dpi(self) -> None:
        """Lee DPI y número de páginas de la entrada en una sola apertura.

        El rango de páginas necesita el recuento de cada PDF para repartirse
        entre archivos, y el DPI ya obligaba a abrirlos todos: hacer las dos
        lecturas con el mismo handle evita una segunda pasada por el lote.
        """
        from app.vision.pdf_loader import PdfPageRenderer

        self._detected_dpis = {}
        self._input_page_counts = []
        for p in self._pdf_paths:
            try:
                with PdfPageRenderer(p) as renderer:
                    self._detected_dpis[str(p.resolve())] = renderer.detect_dpi(
                        default=600
                    )
                    self._input_page_counts.append(renderer.page_count())
            except Exception:  # noqa: BLE001 - PDF inválido, se sigue
                self._input_page_counts.append(0)
                continue
        if self._pdf_paths:
            self._detected_dpi = self._detected_dpis.get(
                str(self._pdf_paths[0].resolve()), 200
            )
        self._reset_page_range()

    def _refresh_input_summary(self) -> None:
        n = len(self._pdf_paths)
        if not n:
            self.input_edit.setText("")
            return
        names = ", ".join(p.name for p in self._pdf_paths)
        self.input_edit.setText(names)
        self.input_edit.setToolTip("\n".join(str(p) for p in self._pdf_paths))

    def _page_range(self) -> PageRange:
        """Rango elegido, contando el lote de corrido.

        Un final que llega a la última página del lote se devuelve abierto:
        es el mismo tramo y así el rango se reconoce como completo (nadie
        vuelve a contar páginas y los mensajes lo dicen en esos términos),
        aunque el control siga mostrando el número real.
        """
        last = self.page_to_spin.value()
        return PageRange(
            first=self.page_from_spin.value(),
            last=None if last >= self._batch_total_pages() else last,
        )

    def _batch_total_pages(self) -> int:
        """Páginas de toda la entrada, antes de aplicar el rango."""
        return sum(self._input_page_counts)

    def _batch_slices(self) -> list[FileSlice]:
        """Tramos de la entrada que el rango deja dentro."""
        return slice_batch(
            self._pdf_paths, self._input_page_counts, self._page_range()
        )

    def _resolved_paths(self) -> list[Path]:
        """Archivos que aportan al menos una página al rango elegido."""
        return [item.path for item in self._batch_slices()]

    @staticmethod
    def _set_spin_silently(spin: QSpinBox, value: int) -> None:
        """Cambia un control sin reentrar en su propio manejador."""
        spin.blockSignals(True)
        spin.setValue(value)
        spin.blockSignals(False)

    def _on_page_from_changed(self, value: int) -> None:
        # Los dos extremos se arrastran: subir el inicio por encima del final
        # deja un tramo de una página en vez de un rango imposible.
        if value > self.page_to_spin.value():
            self._set_spin_silently(self.page_to_spin, value)
        self._refresh_page_range_label()
        self._refresh_estimate()

    def _on_page_to_changed(self, value: int) -> None:
        if value < self.page_from_spin.value():
            self._set_spin_silently(self.page_from_spin, value)
        self._refresh_page_range_label()
        self._refresh_estimate()

    def _reset_page_range(self) -> None:
        """Deja el rango cubriendo la entrada actual, de la primera a la última.

        Se llama al cambiar los archivos: los números del rango anterior no
        señalan las mismas páginas en otro lote, así que conservarlos
        recortaría la corrida sin que nadie lo haya pedido.
        """
        top = max(1, self._batch_total_pages())
        for spin in (self.page_from_spin, self.page_to_spin):
            spin.blockSignals(True)
            spin.setMaximum(top)
            spin.blockSignals(False)
        self._set_spin_silently(self.page_from_spin, 1)
        self._set_spin_silently(self.page_to_spin, top)
        self._refresh_page_range_label()

    def _refresh_page_range_label(self) -> None:
        """Indica cuántas páginas del lote deja seleccionadas el rango."""
        total = self._batch_total_pages()
        if not total:
            self.page_range_label.setText("")
            return
        selected = total_pages(self._batch_slices())
        self.page_range_label.setText(
            f"de {total} pág." if selected == total
            else f"{selected} de {total} pág."
        )

    def _refresh_estimate(self) -> None:
        slices = self._batch_slices()
        pages = total_pages(slices)
        if pages and self._ms_per_page:
            seconds = pages * self._ms_per_page / 1000.0
            self.estimate_label.setText(
                f"Tiempo estimado: {_format_clock(seconds)}  "
                f"{pages} páginas  {len(slices)} archivos"
            )
        elif slices:
            self.estimate_label.setText("Estimación no disponible")
        elif self._pdf_paths:
            self.estimate_label.setText(
                f"El rango ({self._page_range().label()}) no incluye ninguna "
                f"página del lote"
            )
        else:
            self.estimate_label.setText("")

    # ── Acciones ────────────────────────────────────────────────────────

    def _refresh_templates(self) -> None:
        manager = TemplateManager()
        paths = manager.list_templates_with_fallback()
        self.template_combo.clear()
        for path in paths:
            self.template_combo.addItem(path.stem, str(path))

    def _browse_pdfs(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Seleccionar archivos", str(SCRIPT_DIR / "input"), "PDF (*.pdf)"
        )
        if paths:
            self._set_input_paths([Path(p) for p in paths])
            logger.info(f"Entrada seleccionada: {len(paths)} archivo(s)")

    def _browse_template(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Seleccionar plantilla", str(SCRIPT_DIR / "template"),
            "JSON (*.json)"
        )
        if path:
            self._template_path = Path(path)
            self.template_combo.addItem(Path(path).stem, str(path))
            self.template_combo.setCurrentIndex(self.template_combo.count() - 1)
            logger.info(f"Plantilla seleccionada: {path}")

    def _clear_input_folder(self) -> None:
        """Mueve todos los archivos de input/ a la Papelera tras confirmar."""
        processing = self._worker is not None and self._worker.isRunning()
        exporting = (
            self._outputs_worker is not None and self._outputs_worker.isRunning()
        )
        if processing or exporting:
            QMessageBox.warning(
                self,
                "Procesamiento en curso",
                "No se puede vaciar input/ mientras se procesan archivos "
                "o se generan salidas.",
            )
            return

        folder = SCRIPT_DIR / "input"
        if not folder.is_dir():
            QMessageBox.information(
                self, "Vaciar input", "La carpeta input/ no existe."
            )
            return

        files = sorted(path for path in folder.iterdir() if path.is_file())
        if not files:
            QMessageBox.information(
                self, "Vaciar input", "La carpeta input/ ya está vacía."
            )
            return

        answer = QMessageBox.warning(
            self,
            "Confirmar vaciado",
            f"Se moverán {len(files)} archivo(s) de input/ a la "
            "Papelera de reciclaje.\n\n¿Desea continuar?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        moved, failed = send_to_trash(files)
        self._load_default_input()
        logger.info(
            f"Archivos enviados a la Papelera: {len(moved)}; fallidos: {len(failed)}"
        )

        if failed:
            details = "\n".join(f"- {path.name}: {error}" for path, error in failed)
            QMessageBox.warning(
                self,
                "Vaciado incompleto",
                f"Se movieron {len(moved)} de {len(files)} archivo(s).\n\n"
                "No se pudieron mover:\n" + details,
            )
        else:
            QMessageBox.information(
                self,
                "Vaciado completado",
                f"{len(moved)} archivo(s) movido(s) a la Papelera de reciclaje.",
            )

    def _clear_output_folder(self) -> None:
        """Mueve todo el contenido de output/ a la Papelera tras confirmar."""
        processing = self._worker is not None and self._worker.isRunning()
        preprocessing = (
            self._preprocess_worker is not None
            and self._preprocess_worker.isRunning()
        )
        exporting = (
            self._outputs_worker is not None and self._outputs_worker.isRunning()
        )
        if processing or preprocessing or exporting:
            QMessageBox.warning(
                self,
                "Procesamiento en curso",
                "No se puede vaciar output/ mientras se procesan archivos "
                "o se generan salidas.",
            )
            return

        folder = SCRIPT_DIR / "output"
        if not folder.is_dir():
            QMessageBox.information(
                self, "Vaciar output", "La carpeta output/ no existe."
            )
            return

        contents = sorted(folder.iterdir(), key=lambda path: path.name.lower())
        if not contents:
            QMessageBox.information(
                self, "Vaciar output", "La carpeta output/ ya está vacía."
            )
            return

        answer = QMessageBox.warning(
            self,
            "Confirmar vaciado",
            f"Se moverán {len(contents)} elemento(s) de output/ a la "
            "Papelera de reciclaje. Esto incluye todas las corridas "
            "exportadas.\n\n¿Desea continuar?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        moved, failed = send_to_trash(contents)
        self._corrida_dir = None
        logger.info(
            f"Contenido de output enviado a la Papelera: {len(moved)}; "
            f"fallidos: {len(failed)}"
        )

        if failed:
            details = "\n".join(
                f"- {path.name}: {error}" for path, error in failed
            )
            QMessageBox.warning(
                self,
                "Vaciado incompleto",
                f"Se movieron {len(moved)} de {len(contents)} elemento(s).\n\n"
                "No se pudieron mover:\n" + details,
            )
        else:
            QMessageBox.information(
                self,
                "Vaciado completado",
                f"{len(moved)} elemento(s) de output/ movido(s) a la "
                "Papelera de reciclaje.",
            )

    def _open_template_editor(self) -> None:
        """Abre el editor visual usando el mismo Python de la aplicación."""
        editor_script = SCRIPT_DIR / "run_editor.py"
        if not editor_script.is_file():
            QMessageBox.warning(
                self,
                "Editor no disponible",
                f"No se encontró el editor de plantillas:\n{editor_script}",
            )
            return

        try:
            result = QProcess.startDetached(
                sys.executable,
                [str(editor_script)],
                str(SCRIPT_DIR),
            )
            started = result[0] if isinstance(result, (tuple, list)) else result
        except Exception as exc:  # noqa: BLE001 - acción no crítica
            logger.error(f"No se pudo abrir el editor de plantillas: {exc}")
            started = False

        if not started:
            QMessageBox.warning(
                self,
                "Editor no disponible",
                "No se pudo iniciar el editor de plantillas.",
            )
            return
        logger.info("Editor de plantillas abierto")

    def _open_csv_viewer(self) -> None:
        """Abre el visor de corridas como una ventana independiente."""
        if self._csv_viewer is None:
            self._csv_viewer = CsvViewerWindow(SCRIPT_DIR / "output", self)
            self._csv_viewer.setWindowIcon(self.windowIcon())
        self._csv_viewer.show()
        self._csv_viewer.raise_()
        self._csv_viewer.activateWindow()

    def _separator_value(self) -> list[str] | None:
        """Devuelve las claves para generar_pdfs según las casillas."""
        separator = []
        if self.matricula_check.isChecked():
            separator.append("avion")
        if self.mes_check.isChecked():
            separator.append("mes")
        return separator or None

    def _csv_date_mode(self) -> str:
        """Política reversible usada únicamente al representar el CSV."""
        return self.csv_date_mode_combo.currentData() or CSV_DATE_SPECIFIC

    def _on_csv_date_mode_changed(self, _index: int) -> None:
        """Actualiza tabla y CSV existente sin reprocesar OCR ni PDFs."""
        if not self._reports:
            return
        self._populate_table(self._reports)
        if self._outputs_worker is not None and self._outputs_worker.isRunning():
            self._pending_csv_refresh = True
            self.status_label.setText(
                "Cambio de fecha CSV en cola…"
            )
            return
        self._rewrite_current_csv()

    def _rewrite_current_csv(self) -> None:
        """Reescribe solo el CSV de la corrida con la política seleccionada."""
        if not self._reports or self._corrida_dir is None:
            return
        template = self._processed_template or self._load_template()
        if template is None:
            logger.warning("No se pudo actualizar el CSV: falta la plantilla")
            return
        run_dir = Path(self._corrida_dir)
        csv_path = run_dir / "datos" / f"{run_dir.name}.CSV"
        try:
            from app.reports.dual_csv import write_minimal_csv
            from app.reports.outputs import complete_csv_path

            full_csv_path = complete_csv_path(csv_path)
            CsvReporter().write(
                self._reports,
                full_csv_path,
                template,
                date_mode=self._csv_date_mode(),
            )
            write_minimal_csv(
                full_csv_path,
                csv_path,
                self._important_columns_for_export(template),
            )
        except Exception as exc:  # noqa: BLE001 - actualización opcional
            logger.error(f"No se pudo actualizar la fecha del CSV: {exc}")
            self.status_label.setText("Error al actualizar la fecha del CSV.")
            return
        self.status_label.setText(
            f"CSV actualizado: {self.csv_date_mode_combo.currentText()}"
        )
        logger.info(
            f"CSV actualizado sin OCR ({self._csv_date_mode()}): {csv_path}"
        )

    def _export_options(
        self,
        reuse_dir: bool = False,
        skip_pdfs: bool = False,
    ) -> OutputOptions:
        """Captura opciones y datos de la corrida sin tocar el OCR.

        Args:
            reuse_dir: Si ``True`` (re-export), las salidas se escriben
                sobre la carpeta de la corrida actual (``self._corrida_dir``)
                en vez de crear una carpeta nueva.
            skip_pdfs: Si ``True`` (corrida cancelada), se guardan solo
                los datos (CSV, JSON, stats) sin generar PDFs.
        """
        template = self._processed_template or self._load_template()
        if template is None:
            raise ValueError("No hay una plantilla válida para exportar")
        from app.reports.outputs import OutputOptions

        return OutputOptions(
            template=template,
            output_root=SCRIPT_DIR / "output",
            dpi=self._processed_dpi or self._detected_dpi,
            crop_padding=(
                self._config.crop_padding if self._config is not None else 0.01
            ),
            separar_por=tuple(self._separator_value() or ()),
            un_solo_pdf=self.radio_unico.isChecked(),
            discrepancias=self.discrepancias_check.isChecked(),
            # "Visualizar campos" pertenece únicamente a la vista previa.
            debug=False,
            run_dir=self._corrida_dir if reuse_dir else None,
            skip_pdfs=skip_pdfs,
            csv_date_mode=self._csv_date_mode(),
            important_csv_columns=tuple(
                self._important_columns_for_export(template)
            ),
        )

    def _important_columns_for_export(self, template: Template) -> list[str]:
        """Columnas del CSV mínimo, independientes del dataset completo."""
        columns = CsvReporter.columns_for_fields(
            [field.id for field in template.fields],
            skip_ids=frozenset(
                field.id
                for field in template.fields
                if field.type.value == "signature"
            ),
        )
        selected = self._current_important_columns(columns)
        return [column for column in columns if column in selected]

    def _load_template(self) -> Template | None:
        selected = self.template_combo.currentData()
        if not selected or not Path(selected).exists():
            return None
        try:
            return TemplateManager().load(Path(selected))
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"No se pudo cargar la plantilla: {exc}")
            return None

    def _current_processing_config(self) -> AppConfig:
        """Captura las opciones compartidas por preprocesamiento y OCR."""
        return AppConfig(
            dpi=200,
            deskew=self.deskew_check.isChecked(),
            align=self.align_check.isChecked(),
            ocr_engine="paddle",
            ocr_lang="en",
            date_engine_name="",
            ocr_rec_model="PP-OCRv5_mobile_rec",
            ocr_det_model="PP-OCRv6_medium_det",
            remove_printed=self.remove_printed_check.isChecked(),
            crop_preprocess=self.crop_preprocess_check.isChecked(),
            date_ocr_fallback=False,
            date_slot_ocr=False,
            date_dynamic_geometry=True,
            vlm_enabled=False,
            verify_fleet=self.fleet_check.isChecked(),
            fleet_file=SCRIPT_DIR / FLEET_FILENAME,
        )

    def _start_preprocessing(self) -> None:
        """Ejecuta solo el preprocesamiento y actualiza la vista previa."""
        if not self._pdf_paths:
            QMessageBox.warning(
                self, "Aviso", "Seleccione al menos un PDF o use la carpeta input/."
            )
            return
        if (
            self._worker is not None and self._worker.isRunning()
        ) or (
            self._preprocess_worker is not None
            and self._preprocess_worker.isRunning()
        ) or (
            self._outputs_worker is not None and self._outputs_worker.isRunning()
        ):
            return

        slices = self._batch_slices()
        if not slices:
            QMessageBox.warning(self, "Aviso", self._empty_range_message())
            return
        resolved = [item.path for item in slices]

        self._config = self._current_processing_config()
        self._preprocess_geometry = {}
        self._preprocessed_active = False
        # El rango numera el lote completo, así que el worker recibe la
        # entrada entera y lo reparte él: recortar antes lo renumeraría.
        worker = PreprocessWorker(
            self._pdf_paths,
            self._config,
            page_range=self._page_range(),
            reference_page=self.ref_spin.value(),
            parent=self,
        )
        self._preprocess_worker = worker
        worker.progress.connect(self._on_preprocess_progress)
        worker.page_ready.connect(self._on_preprocessed_page)
        worker.succeeded.connect(self._on_preprocess_succeeded)
        worker.failed.connect(self._on_preprocess_failed)
        worker.finished.connect(self._on_preprocess_thread_finished)
        worker.finished.connect(worker.deleteLater)

        self.btn_process.setEnabled(False)
        self.btn_preprocess.setEnabled(False)
        self.btn_export.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        total = total_pages(slices)
        self._set_file_page_counts(slices)
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(0)
        self._total_global = total
        self._done_global = 0
        self._run_started = time.monotonic()
        self._spinner_active = True
        self._timer.start()
        self.status_label.setText("Preprocesando…")
        estimate = total * self._ms_per_page / 1000.0 if total else None
        self._set_time_summary(0.0, estimate, estimate)
        worker.start()

    def _empty_range_message(self) -> str:
        """Explica por qué no hay nada que procesar con el rango actual."""
        total = self._batch_total_pages()
        if not total:
            return "No hay archivos para procesar."
        return (
            f"El rango elegido ({self._page_range().label()}) no incluye "
            f"ninguna página: el lote tiene {total} en total."
        )

    def _confirm_discard_results(self) -> bool:
        """Pide confirmación antes de borrar de la vista una corrida previa.

        Los archivos guardados en ``output/`` no se tocan; lo que se pierde es
        la tabla, la vista previa y el avance por archivo en pantalla.
        """
        if not self._reports:
            return True
        answer = QMessageBox.question(
            self,
            "Procesar de nuevo",
            "Ya hay un procesamiento en pantalla.\n\n"
            "Al procesar de nuevo se borran de la vista la tabla, la vista "
            "previa y el avance por archivo de la corrida actual. Los "
            "archivos ya guardados en output/ no se borran.\n\n"
            "¿Desea continuar?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _clear_results_display(self) -> None:
        """Deja la pantalla sin resultados, sin tocar lo escrito en disco."""
        self._table_timer.stop()
        self._table_pending = []
        self.table.setRowCount(0)
        self.table.setColumnCount(0)
        self._update_duplicate_summary([])
        self.csv_columns_toggle.setEnabled(False)
        self.csv_columns_toggle.setVisible(False)
        self._clear_times()

    def _start_processing(self) -> None:
        if not self._pdf_paths:
            QMessageBox.warning(
                self, "Aviso", "Seleccione al menos un PDF o use la carpeta input/."
            )
            return
        template = self._load_template()
        if template is None:
            QMessageBox.warning(self, "Aviso", "Seleccione una plantilla válida.")
            return
        if (self._worker is not None and self._worker.isRunning()) or (
            self._preprocess_worker is not None
            and self._preprocess_worker.isRunning()
        ) or (
            self._outputs_worker is not None and self._outputs_worker.isRunning()
        ):
            return

        slices = self._batch_slices()
        if not slices:
            QMessageBox.warning(self, "Aviso", self._empty_range_message())
            return
        resolved = [item.path for item in slices]
        if not self._confirm_discard_results():
            return

        self._config = self._current_processing_config()
        # Asociar la plantilla y el DPI al resultado evita que una edición de
        # controles durante el procesamiento cambie la exportación posterior.
        self._processed_template = template
        self._processed_dpi = self._config.dpi
        self._reports = None
        self._preview_results = {}
        self._corrida_dir = None
        self._pending_export = False
        self._pending_csv_refresh = False
        self._last_run_cancelled = False

        selected_threads = self.threads_spin.value()
        effective_threads = self._effective_threads(selected_threads)
        workers, threads = recommended_parallelism(effective_threads)

        # Igual que en el preprocesado: el worker recibe el lote completo y
        # aplica el rango, que está numerado sobre toda la entrada.
        self._worker = PipelineWorker(
            self._pdf_paths,
            Path(self.template_combo.currentData()),
            self._config,
            page_range=self._page_range(),
            reference_page=self.ref_spin.value(),
            workers=workers,
            cpu_threads=threads,
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.file_started.connect(self._on_file_started)
        self._worker.file_progress.connect(self._on_file_progress)
        self._worker.file_finished.connect(self._on_file_finished)
        self._worker.succeeded.connect(self._on_succeeded)
        self._worker.failed.connect(self._on_failed)
        # Sin esto el hilo terminado quedaba retenido hasta la corrida
        # siguiente, con todos los reportes del lote dentro.
        self._worker.finished.connect(self._on_pipeline_thread_finished)
        self._worker.finished.connect(self._worker.deleteLater)

        self.btn_process.setEnabled(False)
        self.btn_preprocess.setEnabled(False)
        self.btn_export.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        total = total_pages(slices)
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(0)
        self._clear_results_display()

        self._total_global = total
        self._set_file_page_counts(slices)
        self._prepare_file_rows(resolved)
        self._done_global = 0
        self._last_done = 0
        self._run_started = time.monotonic()
        self._spinner_active = True
        self._timer.start()
        self.status_label.setText("Procesando…")
        estimate = self._total_global * self._ms_per_page / 1000.0
        self._set_time_summary(0.0, estimate, estimate)

        logger.info(
            f"Iniciando procesamiento: {len(resolved)} archivo(s), "
            f"{self._total_global} página(s) ({self._page_range().label()}), "
            f"200 DPI base / hasta 600 DPI en fechas por PDF, "
            f"{effective_threads} hilos efectivos "
            f"({workers} worker(s) x {threads})"
        )
        self._worker.start()

    # ── Slots ───────────────────────────────────────────────────────────

    def _request_cancel(self) -> None:
        """Pide la cancelación ordenada del pipeline en curso."""
        worker = self._worker
        message = "Cancelando… (las páginas en vuelo terminan y se guardan)"
        if worker is None or not worker.isRunning():
            worker = self._preprocess_worker
            message = "Cancelando preprocesamiento…"
        if worker is None or not worker.isRunning():
            return
        worker.requestInterruption()
        self.btn_cancel.setEnabled(False)
        self.status_label.setText(message)

    def _on_preprocess_progress(
        self, done: int, total: int, message: str
    ) -> None:
        if total > 0:
            self.progress.setRange(0, total)
            self.progress.setValue(done)
            self._done_global = done
        self.status_label.setText(message)

    def _on_preprocessed_page(
        self, pdf_path: str, page_number: int, geometry
    ) -> None:
        """Guarda la geometría de una página y la muestra si está en pantalla.

        Se guardan unos pocos flotantes por página en vez de la imagen: el
        visor rasteriza bajo demanda y aplica esta misma geometría, igual que
        hace con las páginas ya procesadas.
        """
        key = (pdf_path, page_number)
        self._preprocess_geometry[key] = geometry
        if self._preview_pdf is not None and key == (
            str(self._preview_pdf),
            self._preview_page,
        ):
            self._preview_loader.requested.emit(page_number, pdf_path, geometry)

    def _on_preprocess_succeeded(self, cancelled: bool) -> None:
        elapsed = (
            time.monotonic() - self._run_started
            if self._run_started is not None
            else None
        )
        self._preprocessed_active = bool(self._preprocess_geometry)
        self._timer.stop()
        self._run_started = None
        self._spinner_active = False
        self.busy_label.setText("")
        self.btn_process.setEnabled(True)
        self.btn_preprocess.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.btn_export.setEnabled(bool(self._reports))
        self.progress.setRange(0, max(1, self._total_global))
        self.progress.setValue(
            self._total_global if not cancelled else self._done_global
        )
        self._set_time_summary(
            elapsed,
            0.0 if not cancelled else None,
            elapsed if not cancelled else None,
        )
        self.status_label.setText(
            "Preprocesamiento cancelado."
            if cancelled
            else "Preprocesamiento terminado. Puede revisar las páginas."
        )

    def _on_preprocess_failed(self, message: str) -> None:
        elapsed = (
            time.monotonic() - self._run_started
            if self._run_started is not None
            else None
        )
        self._timer.stop()
        self._run_started = None
        self._spinner_active = False
        self.busy_label.setText("")
        self.btn_process.setEnabled(True)
        self.btn_preprocess.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self._set_time_summary(elapsed, None, None)
        self.status_label.setText("Preprocesamiento con errores.")
        logger.error(f"Fallo de preprocesamiento: {message}")
        QMessageBox.critical(self, "Error de preprocesamiento", message)

    def _on_preprocess_thread_finished(self) -> None:
        self._preprocess_worker = None

    def _on_pipeline_thread_finished(self) -> None:
        self._worker = None

    def _on_file_started(self, index: int, total: int, name: str) -> None:
        """Activa la fila del archivo en curso en el panel de tiempos."""
        self.empty_times_label.setVisible(False)
        self._ensure_file_rows(total)
        row = self._file_rows.get(index - 1)
        if row is None:
            return
        font = row["name"].font()
        font.setBold(True)
        row["name"].setFont(font)
        self._set_row_name(row, name)
        self._set_row_pages(row, 0, self._pages_of_file(index - 1))
        row["secs"].setText("…")
        self._row_started[index - 1] = time.monotonic()
        self._current_file_index = index

    def _on_file_finished(self, index: int, report) -> None:
        """Cierra la fila del archivo con su tiempo real."""
        row = self._file_rows.get(index - 1)
        if row is not None:
            font = row["name"].font()
            font.setBold(False)
            row["name"].setFont(font)
            self._row_ms[index - 1] = report.processing_ms
            self._set_row_pages(row, len(report.pages), len(report.pages))
            row["secs"].setText(_format_clock(report.processing_ms / 1000.0))
        if self._current_file_index == index:
            self._current_file_index = 0

    def _on_timer_tick(self) -> None:
        if self._spinner_active:
            self.busy_label.setText(_SPINNER[self._spinner_idx % len(_SPINNER)])
            self._spinner_idx += 1
        elif self.busy_label.text():
            self.busy_label.setText("")
        if self._run_started is None:
            return
        elapsed = time.monotonic() - self._run_started
        remaining = None
        total = None
        if self._total_global > 0:
            remaining = estimate_remaining_seconds(
                total_pages=self._total_global,
                completed_pages=self._done_global,
                elapsed_seconds=elapsed,
                cached_ms_per_page=self._ms_per_page,
            )
            total = elapsed + remaining
        self._set_time_summary(elapsed, remaining, total)
        # Reloj en vivo de cada archivo abierto: repartiendo un PDF por
        # proceso hay varios corriendo a la vez, no solo el último iniciado.
        for index, started in self._row_started.items():
            if index in self._row_ms:
                continue
            row = self._file_rows.get(index)
            if row is not None:
                row["secs"].setText(_format_clock(time.monotonic() - started))

    def _on_progress(self, done: int, total: int, message: str) -> None:
        if total > 0:
            if self.progress.maximum() != total:
                self.progress.setRange(0, total)
            self.progress.setValue(done)
            if done > self._last_done:
                self._last_done = done
            self._done_global = done
        self.status_label.setText(message)

    def _on_succeeded(self, reports: list[ValidationReport]) -> None:
        elapsed = (
            time.monotonic() - self._run_started
            if self._run_started is not None
            else None
        )
        self._timer.stop()
        self._reports = reports
        self._vlm_stats = list(getattr(self._worker, "vlm_stats", []) or [])
        self._preview_results = {
            (str(Path(report.pdf_path).resolve()), page.page_number): page
            for report in reports
            for page in report.pages
        }
        self._set_preview_documents([Path(report.pdf_path) for report in reports])
        self._corrida_dir = None
        self._pending_export = False
        # El botón Exportar queda disponible en cuanto el OCR termina, sin
        # esperar a que termine la generación de salidas de fondo: re-exportar
        # es independiente y se encola si ya hay una generación en curso.
        self.btn_process.setEnabled(False)
        self.btn_preprocess.setEnabled(False)
        self.btn_export.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self._update_performance(reports, elapsed)
        self._refresh_estimate()

        cancelled_any = any(getattr(r, "cancelled", False) for r in reports)
        if elapsed is not None:
            self._set_time_summary(
                elapsed,
                None if cancelled_any else 0.0,
                None if cancelled_any else elapsed,
            )
        self._run_started = None

        self._last_run_cancelled = cancelled_any
        total_pages = sum(len(r.pages) for r in reports)
        ok = sum(r.summary.get("ok_pages", 0) for r in reports)
        warn = sum(r.summary.get("warning_pages", 0) for r in reports)
        err = sum(r.summary.get("error_pages", 0) for r in reports)
        blank = sum(r.summary.get("blank_pages", 0) for r in reports)
        # Reloj de la corrida, no la suma de relojes: con un proceso por
        # archivo los tiempos se solapan y sumarlos daba un total imposible,
        # varias veces mayor que lo que el usuario esperó. Se prefiere el
        # cronómetro de la ventana, que es el que estuvo a la vista.
        total_ms = (
            elapsed * 1000 if elapsed is not None
            else CsvReporter.run_wall_ms(reports)
        )
        calib_ms = sum(r.calibration_ms for r in reports)
        summary = (
            f"OK: {ok} | WARNING: {warn} | ERROR: {err} | "
            f"En blanco: {blank} | "
            f"Calibración: {calib_ms / 1000:.2f} s + "
            f"Procesado: {max(0.0, total_ms - calib_ms) / 1000:.2f} s = "
            f"Total: {total_ms / 1000:.2f} s"
        )
        state = "cancelado" if cancelled_any else "terminado"
        logger.info(
            f"Procesamiento {state} "
            f"({len(reports)} archivos, {total_pages} páginas). "
            f"{summary}"
        )

        if cancelled_any:
            # La corrida se guarda hasta donde se canceló (CSV/JSON/stats),
            # sin generar PDFs; la pantalla queda limpia para poder
            # procesar los archivos restantes.
            self._clear_results_display()
            self.status_label.setText(
                "Procesamiento cancelado — guardando resultados parciales…"
            )
            # El rango de páginas no se toca: para seguir donde se cortó hay
            # que ajustarlo a mano antes de volver a procesar.
            self._timer.start()
            self._start_outputs(reports, context="proceso", skip_pdfs=True)
            return

        self._populate_table(reports)
        self._populate_times(reports)
        if reports and reports[0].pages:
            self._show_preview_page(
                reports[0].pages[0].page_number, Path(reports[0].pdf_path)
            )
        self.status_label.setText("Generando salidas…")
        self._timer.start()
        self._start_outputs(reports, context="proceso")

    def _start_outputs(
        self,
        reports: list[ValidationReport],
        context: str,
        skip_pdfs: bool = False,
    ) -> None:
        """Inicia la escritura de salidas sin bloquear el hilo de la GUI.

        Args:
            context: ``"proceso"`` (automática tras el OCR) o ``"export"``
                (re-export manual).
            skip_pdfs: Si la corrida fue cancelada, solo se guardan los
                datos (CSV/JSON/stats) sin generar PDFs.
        """
        if self._outputs_worker is not None and self._outputs_worker.isRunning():
            return

        self._outputs_context = context
        try:
            options = self._export_options(
                reuse_dir=(context == "export"), skip_pdfs=skip_pdfs
            )
        except Exception as exc:  # noqa: BLE001 - se muestra en la GUI
            self._on_outputs_failed(str(exc))
            self._on_outputs_thread_finished()
            return

        worker = OutputsWorker(
            reports,
            options,
            vlm_stats=self._vlm_stats,
            parent=self,
        )
        self._outputs_worker = worker
        worker.succeeded.connect(self._on_outputs_written)
        worker.failed.connect(self._on_outputs_failed)
        worker.progress.connect(self._on_outputs_stage)
        worker.finished.connect(self._on_outputs_thread_finished)
        worker.finished.connect(worker.deleteLater)
        self.btn_process.setEnabled(False)
        self.btn_preprocess.setEnabled(False)
        self.btn_export.setEnabled(False)
        self._spinner_active = True
        # La barra general representa siempre páginas del lote; la exportación
        # no debe sustituir ese total por una barra indeterminada.
        self.progress.setRange(0, max(1, self._total_global))
        self.progress.setValue(self._done_global or self._total_global)
        worker.start()

    def _on_outputs_stage(self, message: str, percent: int) -> None:
        """Actualiza la barra y el estado con la fase de exportación."""
        self.progress.setRange(0, max(1, self._total_global))
        self.progress.setValue(self._done_global or self._total_global)
        self.status_label.setText(f"Generando salidas… {message}")

    def _on_outputs_written(self, output_dir: Path) -> None:
        """Actualiza la interfaz cuando termina una exportación."""
        self._corrida_dir = Path(output_dir)
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        if self._outputs_context == "export":
            self.status_label.setText(f"Exportación terminada: {output_dir.name}")
            logger.info(f"Exportación completada en: {output_dir}")
        elif self._last_run_cancelled:
            self.status_label.setText(
                "Procesamiento cancelado — resultados parciales guardados "
                f"en {output_dir.name} (sin PDF)."
            )
            logger.info(f"Corrida cancelada guardada (datos sin PDF) en: {output_dir}")
        else:
            self.status_label.setText(
                "Procesamiento terminado. Puede cambiar la separación y exportar."
            )
            logger.info(f"Outputs generados en: {output_dir}")

    def _on_outputs_failed(self, message: str) -> None:
        """Registra un error de salidas sin interrumpir la interfaz."""
        context = self._outputs_context
        logger.error(f"Error generando outputs: {message}")
        if context == "export":
            self.status_label.setText("Error al exportar.")
            details = message.splitlines()[0] if message else "Error desconocido"
            QMessageBox.critical(self, "Error al exportar", details)
        else:
            self.status_label.setText(
                "Procesamiento terminado con error al generar salidas."
            )

    def _on_outputs_thread_finished(self) -> None:
        """Libera los controles cuando el hilo de salidas ya terminó."""
        self._outputs_worker = None
        self._outputs_context = None
        self._timer.stop()
        self._spinner_active = False
        self.busy_label.setText("")
        self.progress.setRange(0, 100)
        if self._closing:
            # La ventana está cerrando: ni se reactivan los botones ni se
            # encola otra exportación, que dejaría el cierre sin terminar.
            self._pending_export = False
            self._pending_csv_refresh = False
            return
        self.btn_cancel.setEnabled(False)
        self.btn_process.setEnabled(True)
        self.btn_preprocess.setEnabled(True)
        # Tras una corrida cancelada no hay Exportar: da la opción de
        # procesar los archivos restantes en vez de hacer PDFs parciales.
        self.btn_export.setEnabled(bool(self._reports) and not self._last_run_cancelled)
        if self._pending_export and not self._last_run_cancelled:
            self._pending_export = False
            # La reexportación ya escribirá el CSV con la selección más
            # reciente; no se necesita una escritura paralela adicional.
            self._pending_csv_refresh = False
            self._exportar()
        elif self._pending_export:
            self._pending_export = False
        elif self._pending_csv_refresh:
            self._pending_csv_refresh = False
            self._rewrite_current_csv()

    def _update_performance(
        self, reports: list[ValidationReport], elapsed_seconds: float | None
    ) -> None:
        """Aprende throughput de pared; no suma tiempos de workers paralelos."""
        pages = sum(len(r.pages) for r in reports)
        measured = wall_ms_per_page(elapsed_seconds or 0.0, pages)
        if measured is not None:
            self._ms_per_page = max(1.0, measured)
            _save_ms_per_page(self._ms_per_page)

    def _exportar(self) -> None:
        """Regenera CSV, JSON y PDFs con las opciones actuales, sin
        reprocesar los archivos ya analizados."""
        if not self._reports:
            QMessageBox.information(self, "Exportar", "Primero procese los archivos.")
            return
        if self._outputs_worker is not None and self._outputs_worker.isRunning():
            # Ya hay una generación en curso (p. ej. la automática del
            # procesamiento): se encola y se ejecuta apenas termine.
            self._pending_export = True
            self.status_label.setText(
                "Exportación en cola… (termina la generación en curso)"
            )
            logger.info("Re-export en cola hasta que termine la generación en curso")
            return
        # Las casillas se capturan al hacer clic en Exportar, no al procesar:
        # cambiar la separación nunca vuelve a ejecutar OCR. El re-export se
        # escribe sobre la carpeta de la corrida actual (self._corrida_dir).
        self.status_label.setText("Exportando salidas…")
        self._start_outputs(self._reports, context="export")

    def _on_failed(self, message: str) -> None:
        elapsed = (
            time.monotonic() - self._run_started
            if self._run_started is not None
            else None
        )
        self._timer.stop()
        self._run_started = None
        self._spinner_active = False
        self.busy_label.setText("")
        self.btn_process.setEnabled(True)
        self.btn_preprocess.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self._set_time_summary(elapsed, None, None)
        self.status_label.setText("Procesamiento con errores.")
        # Conserva en el panel de tiempos lo que alcanzó a procesarse.
        partial = getattr(self._worker, "reports", None)
        if partial:
            self._populate_times(list(partial))
        logger.error(f"Fallo: {message}")
        QMessageBox.critical(self, "Error de procesamiento", message)

    def _populate_table(self, reports: list[ValidationReport]) -> None:
        """Prepara las filas y las inserta por lotes para no congelar la UI.

        El llenado completo de miles de filas bloquea el hilo de interfaz;
        con ``_table_timer`` se inserta un tramo de ``_TABLE_CELL_CHUNK``
        celdas por tick.
        """
        self.table.setUpdatesEnabled(False)
        self.table_sort.suspend()
        try:
            self.table.setRowCount(0)
            self._row_pdfs = []
            self._classify_discrepancies(reports)
            reporter = CsvReporter()
            fields = reporter.fields_for(reports, self._processed_template)
            columns = reporter.columns_for(reports, self._processed_template)
            duplicates = detect_duplicate_log_pages(reports)
            self._update_duplicate_summary(duplicates)
            duplicate_iter = iter(duplicates)
            # El mismo reparto de tiempo que escribe el CSV: la tabla y el
            # archivo no pueden mostrar dos números distintos por página.
            time_factor = reporter.run_time_factor(reports)
            pending: list[
                tuple[
                    int,
                    dict[str, object],
                    dict[str, object],
                    DuplicateLogPage,
                ]
            ] = []
            for report in reports:
                pdf_path = Path(report.pdf_path)
                for page in report.pages:
                    duplicate = next(duplicate_iter)
                    self._row_pdfs.append(pdf_path)
                    row = reporter.row_for_page(
                        report,
                        page,
                        fields,
                        date_mode=self._csv_date_mode(),
                        duplicate=duplicate.duplicate,
                        time_factor=time_factor,
                    )
                    field_results = {field.field_id: field for field in page.fields}
                    pending.append(
                        (
                            len(self._row_pdfs) - 1,
                            row,
                            field_results,
                            duplicate,
                        )
                    )

            self.table.setColumnCount(len(columns))
            self.table.setHorizontalHeaderLabels(columns)
            self.table.setRowCount(len(pending))
            self._table_columns = columns
            self._table_important_field_ids = {
                field.id
                for field in (self._processed_template.fields
                              if self._processed_template is not None else [])
                if field.required
            }
            self._table_important_field_ids.add(_DUP_COLUMN)
            self._table_important_field_ids.add(_DISC_COLUMN)
            # La selección guardada se conserva completa: recortarla contra
            # las columnas de esta corrida perdería las marcas de un CSV con
            # otras columnas la próxima vez que el usuario edite la lista.
            self._selected_important_columns = self._current_important_columns(
                columns
            )
            self.csv_columns_toggle.setEnabled(bool(columns))
            self.csv_columns_toggle.setVisible(bool(columns))
            self._apply_csv_table_view()
            self._table_pending = pending
            if pending:
                self.btn_prev.setEnabled(False)
                self.btn_next.setEnabled(False)
                self._table_timer.start()
            else:
                self.table_sort.reset()
        finally:
            self.table.setUpdatesEnabled(True)

    def _classify_discrepancies(self, reports: list[ValidationReport]) -> None:
        """Marca ``page.discrepancy`` antes de armar las filas de la tabla.

        La columna ``disc`` sale de esa marca. La escritura de salidas la
        calcula también, pero en el hilo de fondo y después de esta tabla: sin
        recorrerla aquí, la pantalla mostraría ``false`` en páginas que el CSV
        marca como discrepancia. Mientras ese hilo corre no se repite, porque
        estaría reescribiendo las mismas marcas que el otro hilo lee.
        """
        if self._processed_template is None:
            return
        if self._outputs_worker is not None and self._outputs_worker.isRunning():
            return
        from app.validation.discrepancias import clasificar_lote

        clasificar_lote(reports, self._processed_template)

    def _update_duplicate_summary(
        self, duplicates: list[DuplicateLogPage]
    ) -> None:
        """Actualiza el contador compacto y su detalle por hover."""
        repeated = [item for item in duplicates if item.duplicate]
        groups: dict[int, list[DuplicateLogPage]] = {}
        for item in duplicates:
            if item.log_number is not None:
                groups.setdefault(item.log_number, []).append(item)
        groups = {
            number: items for number, items in groups.items() if len(items) > 1
        }

        count = len(repeated)
        self.duplicates_label.setText(f"Duplicados: {count}")
        if not count:
            self.duplicates_label.setStyleSheet("color: #57606a;")
            self.duplicates_label.setToolTip(
                "No hay log_number repetidos en el lote procesado."
            )
            return

        self.duplicates_label.setStyleSheet(
            f"color: {_COLORS[Status.WARNING]}; font-weight: 600;"
        )
        lines = [
            f"{count} página(s) duplicada(s) en {len(groups)} log_number:",
        ]
        for number, items in sorted(groups.items()):
            locations = ", ".join(
                f"{Path(item.pdf_path).name} PDF p. {item.page_number}"
                for item in items
            )
            lines.append(
                f"{number:07d} (log page {number % 100:02d}): {locations}"
            )
        self.duplicates_label.setToolTip("\n".join(lines))

    @staticmethod
    def _discrepancy_tooltip(value: str) -> str:
        if value.strip().lower() == "true":
            return "disc=true: la bitácora quedó marcada como discrepancia de firmas."
        return "disc=false: la bitácora cumple las firmas exigidas."

    @staticmethod
    def _duplicate_tooltip(duplicate: DuplicateLogPage) -> str:
        if duplicate.log_number is None:
            return "dup=false: log_number ausente o inválido."
        if duplicate.duplicate:
            return (
                "dup=true: este log_number ya apareció antes en el lote "
                "procesado."
            )
        return "dup=false: primera aparición de este log_number en el lote."

    def _apply_csv_table_view(self, _checked: bool | None = None) -> None:
        """Alterna la tabla entre valores principales y todas las columnas."""
        apply_csv_column_visibility(
            self.table,
            self._table_columns,
            self._table_important_field_ids,
            self.csv_columns_toggle.isChecked(),
            self._selected_important_columns,
        )

    def _rows_per_chunk(self) -> int:
        """Filas que caben en el presupuesto de celdas de un tick."""
        return max(1, _TABLE_CELL_CHUNK // max(1, len(self._table_columns)))

    def _on_table_chunk(self) -> None:
        if not self._table_pending:
            self._table_timer.stop()
            if self._outputs_worker is not None and self._outputs_worker.isRunning():
                self.status_label.setText("Generando salidas…")
            self._update_preview_nav()
            self.table.viewport().update()
            return
        rows = self._rows_per_chunk()
        batch = self._table_pending[:rows]
        del self._table_pending[:rows]
        for row_index, values, field_results, duplicate in batch:
            for col_index, column in enumerate(self._table_columns):
                value = values.get(column, "")
                item = QTableWidgetItem(str(value))
                if column == "page":
                    item.setData(Qt.ItemDataRole.UserRole, int(value))
                    item.setData(
                        Qt.ItemDataRole.UserRole + 1,
                        str(self._row_pdfs[row_index]),
                    )
                field_id = column.removesuffix("_conf")
                field = field_results.get(field_id)
                if column == _DISC_COLUMN:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    item.setToolTip(self._discrepancy_tooltip(str(value)))
                elif column == _DUP_COLUMN:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    item.setToolTip(self._duplicate_tooltip(duplicate))
                    if duplicate.duplicate:
                        font = item.font()
                        font.setBold(True)
                        item.setFont(font)
                        item.setForeground(Qt.GlobalColor.white)
                        item.setBackground(_color_for(Status.WARNING))
                elif field is not None:
                    item.setToolTip(
                        f"Estado: {field.status.value}"
                        + (f"\n{field.comment}" if field.comment else "")
                    )
                    item.setForeground(Qt.GlobalColor.white)
                    item.setBackground(_color_for(field.status))
                self.table.setItem(row_index, col_index, item)
        self.table.viewport().update()
        if self._table_pending:
            done = len(batch)
            total = done + len(self._table_pending)
            self.status_label.setText(f"Construyendo tabla… {done}/{total}")
        else:
            self.table_sort.reset()

    # ── Tiempo por archivo ──────────────────────────────────────────────

    def _clear_times(self) -> None:
        self.empty_times_label.show()
        self._file_rows = {}
        self._row_ms = {}
        self._row_started = {}
        self._current_file_index = 0
        _clear_layout(self.times_vbox)
        for widget in (self.times_container,):
            widget.update()

    def _populate_times(self, reports: list[ValidationReport]) -> None:
        """Estado final del panel: cada archivo al 100 % con sus páginas."""
        _clear_layout(self.times_vbox)
        self._file_rows = {}
        self._row_ms = {}
        self._row_started = {}
        self._current_file_index = 0
        self.empty_times_label.setVisible(not bool(reports))
        if not reports:
            return
        for index, report in enumerate(reports):
            row = self._make_file_row()
            self._set_row_name(
                row, Path(report.pdf_path).name, str(report.pdf_path)
            )
            self._set_row_pages(row, len(report.pages), len(report.pages))
            row["secs"].setText(_format_clock(report.processing_ms / 1000.0))
            self._file_rows[index] = row
            self._row_ms[index] = report.processing_ms
        self.times_vbox.addStretch()

    # ── Vista previa ───────────────────────────────────────────────────

    def _refresh_preview_template(self, _index: int = -1) -> None:
        """Redibuja las casillas de la plantilla sobre la vista actual.

        Cada plantilla recuerda sus propios campos importantes, así que al
        cambiarla se recupera la selección guardada para la nueva.
        """
        self._restore_important_columns()
        self._apply_preview_overlay()

    def _on_fields_toggled(self, _checked: bool) -> None:
        self._apply_preview_overlay()

    def _jump_to_page(self, row: int, _col: int) -> None:
        if self._table_pending:
            return  # la tabla aún se está construyendo
        try:
            page_column = self._table_columns.index("page")
        except ValueError:
            return
        item = self.table.item(row, page_column)
        pdf_value = item.data(Qt.ItemDataRole.UserRole + 1) if item else None
        if item is not None and pdf_value:
            self._show_preview_page(
                int(item.data(Qt.ItemDataRole.UserRole)),
                Path(str(pdf_value)),
            )

    def _update_preview_nav(self) -> None:
        """Sincroniza la paginación global y el indicador de archivo."""
        has_pdf = self._preview_pdf is not None
        global_page = self._preview_global_page()
        global_total = sum(self._preview_document_counts)
        self.btn_prev.setEnabled(has_pdf and global_page > 1)
        self.btn_next.setEnabled(has_pdf and global_page < global_total)
        self.page_edit.setText(str(global_page) if has_pdf else "")
        self.page_edit.setModified(False)
        validator = self.page_edit.validator()
        if isinstance(validator, QIntValidator):
            validator.setTop(max(1, global_total))
        self.page_edit.setEnabled(has_pdf and global_total > 0)
        self.page_total_label.setText(f"de {global_total}")
        index = self._preview_document_index()
        pdf_text = self._preview_pdf.name if has_pdf else "Sin PDF"
        self.preview_file_label.setText(pdf_text)
        self.preview_file_label.setToolTip(
            str(self._preview_pdf) if self._preview_pdf is not None else ""
        )
        pdf_position = f"Archivo {index + 1} de {len(self._preview_documents)}"
        context = (
            f"{pdf_position} · {pdf_text} · Página "
            f"{self._preview_page if has_pdf else 0} de "
            f"{self._preview_total if has_pdf else 0} en el archivo"
        )
        self.preview_context_label.setText(context)

    def _prev_page(self) -> None:
        current = self._preview_global_page()
        if current > 1:
            location = self._preview_location(current - 1)
            if location is not None:
                self._show_preview_page(location[1], location[0])

    def _next_page(self) -> None:
        current = self._preview_global_page()
        if current < sum(self._preview_document_counts):
            location = self._preview_location(current + 1)
            if location is not None:
                self._show_preview_page(location[1], location[0])

    def _draw_template_boxes(
        self,
        pixmap: QPixmap,
        template,
        w: int,
        h: int,
        boxes: dict[str, list[float]] | None = None,
    ) -> None:
        """Dibuja los rectángulos efectivos sobre la página alineada."""
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(0, 120, 215), 2)
        font = QFont()
        font.setPointSize(9)
        font.setBold(True)
        painter.setFont(font)
        important_only = self.important_fields_check.isChecked()
        important_ids = (
            self._current_important_field_ids(template) if important_only else None
        )
        for field in _visible_preview_fields(template, important_only, important_ids):
            coords = (boxes or {}).get(field.id)
            if coords is None or len(coords) != 4:
                coords = (field.x, field.y, field.w, field.h)
            x, y, width, height = coords
            rect = QRectF(x * w, y * h, width * w, height * h)
            painter.setBrush(QColor(0, 140, 220, 30))
            painter.setPen(pen)
            painter.drawRect(rect)
            painter.setBrush(QColor(0, 140, 220, 160))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawText(
                rect.adjusted(2, 2, -2, -2),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                field.id,
            )
        painter.end()

    def _current_preview_result(self):
        """Resultado de la página visible, si ya fue procesada."""
        if self._preview_pdf is None:
            return None
        key = (str(Path(self._preview_pdf).resolve()), self._preview_page)
        return self._preview_results.get(key)

    def _current_preview_geometry(self) -> dict | None:
        """Transformación que convierte la miniatura cruda en la procesada."""
        page = self._current_preview_result()
        if page is None:
            return None
        return {
            "skew_angle": float(page.skew_angle),
            "alignment": page.preview_alignment,
        }

    def _show_preview_page(
        self, page_number: int, pdf_path: Path | None = None
    ) -> None:
        """Pide la página al hilo de fondo y actualiza la navegación.

        El render ya no corre en el hilo de interfaz; mientras llega la
        imagen se conserva la anterior en pantalla.
        """
        pdf_path = pdf_path or self._preview_pdf
        if pdf_path is None:
            return
        if pdf_path != self._preview_pdf:
            # El total se consulta una sola vez por documento.
            try:
                from app.vision.pdf_loader import page_count

                self._preview_total = page_count(pdf_path)
            except Exception:  # noqa: BLE001 - no crítico
                self._preview_total = 0
        if self._preview_total:
            page_number = max(1, min(page_number, self._preview_total))
        self._preview_page = page_number
        self._preview_pdf = pdf_path
        self._update_preview_nav()
        self._preview_pending = (page_number, str(pdf_path))
        # Tras procesar, la geometría guardada en PageResult refleja exactamente
        # la alineación (incluido el anclaje por lote) usada por el OCR, así que
        # tiene prioridad: el preprocesado previo pudo usar otro anclaje.
        geometry = self._current_preview_geometry()
        if geometry is None and self._preprocessed_active:
            geometry = self._preprocess_geometry.get(
                (str(pdf_path), page_number)
            )
        self._preview_loader.requested.emit(
            page_number, str(pdf_path), geometry
        )

    def _on_preview_ready(
        self, page_number: int, pdf_path: str, qimage: QImage | None
    ) -> None:
        """Aplica el render solo si sigue siendo la página pedida."""
        if (page_number, pdf_path) != self._preview_pending:
            return  # respuesta obsoleta (el usuario ya navegó)
        if qimage is None:
            return
        self._set_preview_qimage(qimage)

    def _set_preview_qimage(self, qimage: QImage) -> None:
        """Guarda el render limpio y le aplica el overlay actual."""
        self._preview_base_image = qimage
        self._apply_preview_overlay()

    def _apply_preview_overlay(self) -> None:
        """Repinta los recuadros sobre el render ya disponible.

        Cambiar qué campos se muestran no cambia la página rasterizada, así
        que el overlay se rehace sin volver a pedirla al hilo de render.
        """
        qimage = self._preview_base_image
        if qimage is None:
            return
        pixmap = QPixmap.fromImage(qimage)
        if self.fields_check.isChecked():
            template = self._processed_template or self._load_template()
            if template is not None:
                page = self._current_preview_result()
                self._draw_template_boxes(
                    pixmap,
                    template,
                    qimage.width(),
                    qimage.height(),
                    boxes=page.preview_boxes if page is not None else None,
                )
        self._preview_source_pixmap = pixmap
        self._render_preview_pixmap()

    def _update_preview_zoom_controls(self) -> None:
        """Sincroniza los controles de zoom con la disponibilidad de una imagen."""
        has_image = self._preview_source_pixmap is not None
        self.btn_zoom_out.setEnabled(has_image and self._preview_zoom > 0.4)
        self.btn_zoom_fit.setEnabled(has_image)
        self.btn_zoom_in.setEnabled(has_image and self._preview_zoom < 4.0)
        self.zoom_label.setText(f"{round(self._preview_zoom * 100)}%")

    def _preview_fit_height(self) -> int:
        """Devuelve la altura disponible para el ajuste vertical."""
        height = self.preview_scroll.viewport().height()
        return max(1, height - 4)

    def _render_preview_pixmap(self) -> None:
        """Escala la imagen fuente sin perder resolución para zoom o resize."""
        source = self._preview_source_pixmap
        if source is None or source.isNull():
            return
        target_height = max(
            1, round(self._preview_fit_height() * self._preview_zoom)
        )
        pixmap = source.scaledToHeight(
            target_height, Qt.TransformationMode.SmoothTransformation
        )
        self.preview_label.setPixmap(pixmap)
        self.preview_label.setFixedSize(pixmap.size())
        self._update_preview_zoom_controls()

    def _fit_preview_vertical(self) -> None:
        """Restablece la vista para mostrar la página completa en vertical."""
        self._preview_zoom = 1.0
        self._render_preview_pixmap()

    def _zoom_preview(self, factor: float) -> None:
        """Aplica zoom relativo; las barras de desplazamiento permiten mover la página."""
        self._preview_zoom = min(4.0, max(0.4, self._preview_zoom * factor))
        self._render_preview_pixmap()

    def keyPressEvent(self, event) -> None:
        """Ctrl++ / Ctrl+- para hacer zoom sobre la vista previa."""
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            key = event.key()
            if key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
                self._zoom_preview(1.25)
                event.accept()
                return
            if key in (Qt.Key.Key_Minus,):
                self._zoom_preview(0.8)
                event.accept()
                return
        super().keyPressEvent(event)

    def _install_zoom_shortcuts(self) -> None:
        """Atajos Ctrl++ / Ctrl+- independientes del widget con foco."""
        for seq, factor in (
            (QKeySequence("Ctrl++"), 1.25),
            (QKeySequence("Ctrl+="), 1.25),
            (QKeySequence("Ctrl+-"), 0.8),
        ):
            QShortcut(seq, self,
                      activated=lambda f=factor: self._zoom_preview(f))

    def showEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().showEvent(event)
        QTimer.singleShot(0, self._balance_bottom_splitter)

    def resizeEvent(self, event) -> None:
        """Reajusta la vista también cuando cambia el tamaño de la ventana."""
        super().resizeEvent(event)
        QTimer.singleShot(0, self._balance_bottom_splitter)
        if self._preview_source_pixmap is not None:
            QTimer.singleShot(0, self._render_preview_pixmap)

    # ── Cierre ──────────────────────────────────────────────────────────

    def _running_workers(self) -> list[QThread]:
        """Hilos de trabajo todavía en marcha, si los hay."""
        running: list[QThread] = []
        for worker in (
            self._worker, self._preprocess_worker, self._outputs_worker
        ):
            if worker is None:
                continue
            try:
                if worker.isRunning():
                    running.append(worker)
            except RuntimeError:
                # El objeto C++ ya se destruyó tras ``deleteLater``.
                continue
        return running

    def closeEvent(self, event) -> None:
        """Cierra sin destruir hilos vivos, que abortaría el proceso.

        Cerrar la ventana con OCR, preprocesado o exportación en marcha
        destruía un ``QThread`` en ejecución y Windows mataba el programa
        (0xC0000409). Ahora el cierre pide la parada, deja la ventana viva
        atendiendo eventos mientras el trabajo en vuelo termina, y se
        completa solo cuando ya no queda ningún hilo corriendo.
        """
        running = self._running_workers()
        if running:
            self._begin_shutdown(running)
            event.ignore()
            return
        self._teardown()
        super().closeEvent(event)

    def _begin_shutdown(self, running: list[QThread]) -> None:
        """Pide la parada del trabajo en curso y espera sin congelar la GUI."""
        if not self._closing:
            self._closing = True
            logger.info(
                f"Cierre solicitado: deteniendo {len(running)} tarea(s) en curso"
            )
            for worker in running:
                worker.requestInterruption()
            for button in (
                self.btn_process, self.btn_preprocess,
                self.btn_export, self.btn_cancel,
            ):
                button.setEnabled(False)
            self.status_label.setText(
                "Cerrando… se está deteniendo el trabajo en curso. "
                "Las páginas ya leídas se guardan."
            )
        if not self._shutdown_timer.isActive():
            self._shutdown_timer.start()

    def _on_shutdown_tick(self) -> None:
        """Cierra de verdad en cuanto el último hilo termina."""
        if self._running_workers():
            return
        self._shutdown_timer.stop()
        self.close()

    def _teardown(self) -> None:
        """Detiene temporizadores e hilos propios antes de destruir la ventana.

        Se ejecuta una sola vez: la llaman tanto el cierre de la ventana como
        ``aboutToQuit``, y la segunda pasada no debe tocar nada.
        """
        if self._torn_down:
            return
        self._torn_down = True
        for timer in (
            self._timer, self._table_timer,
            self._log_timer, self._shutdown_timer,
        ):
            timer.stop()
        if self._csv_viewer is not None:
            self._csv_viewer.close()
        self._preview_thread.quit()
        self._preview_thread.wait(5000)
        # Red de seguridad: si algún hilo llegara vivo hasta aquí, esperarlo
        # es preferible a que Qt lo destruya en marcha y aborte el proceso.
        for worker in self._running_workers():
            worker.requestInterruption()
            worker.wait(10000)


def _color_for(status: Status):
    from PySide6.QtGui import QColor

    return QColor(_COLORS[status])

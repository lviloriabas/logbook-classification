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
from collections import deque
from pathlib import Path
from statistics import median

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
    QKeySequence,
    QPainter,
    QPen,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
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
from app.core.parallelism import available_cpu_threads, recommended_parallelism
from app.gui.csv_viewer import (
    CsvColumnModeButton,
    CsvViewerWindow,
    ImportantFieldsButton,
    apply_csv_column_visibility,
)
from app.gui.field_selector import ImportantFieldsDialog
from app.gui.fleet_editor import FLEET_FILENAME, FleetEditorDialog, FleetStore
from app.gui.worker import OutputsWorker, PipelineWorker, PreprocessWorker
from app.models.schemas import Status, ValidationReport
from app.reports.csv_reporter import (
    CSV_DATE_MONTH_END,
    CSV_DATE_SPECIFIC,
    CsvReporter,
)
from app.templates.manager import TemplateManager
from app.templates.schema import Template
from app.utils.io import send_to_trash
from app.validation.duplicates import DuplicateLogPage, detect_duplicate_log_pages

SCRIPT_DIR = Path(__file__).resolve().parents[2]
PERF_CACHE = SCRIPT_DIR / "output" / ".performance.json"
_DEFAULT_MS_PER_PAGE = 2500.0  # costo nominal antes de la primera corrida

_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_TABLE_CHUNK = 400  # filas de la tabla por tick del QTimer
_DUP_COLUMN = "dup"


_COLORS = {
    Status.OK: "#1a7f37",
    Status.WARNING: "#9a6700",
    Status.ERROR: "#cf222e",
}


def _visible_preview_fields(template: Template, important_only: bool):
    """Campos que debe pintar el visor.

    La plantilla ya expresa la importancia mediante ``required``. Esto
    conserva matrícula, log_number, fecha y firmas obligatorias, y oculta
    las celdas auxiliares y licencias opcionales cuando se pide una vista
    simplificada.
    """
    if not important_only:
        return list(template.fields)
    return [field for field in template.fields if field.required]

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
QTableWidget {
    gridline-color: #d8dee4;
    alternate-background-color: #f6f8fa;
}
QHeaderView::section {
    background-color: #eef2f6;
    border: 0;
    border-bottom: 1px solid #c9d1d9;
    padding: 6px 8px;
    font-weight: 600;
}
#timeBar { background-color: #e1e7ee; }
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
"""


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


def _load_zoom_icon(name: str) -> QIcon:
    """Carga un icono de zoom local para que el visor sea consistente en Windows."""
    path = SCRIPT_DIR / "assets" / f"zoom_{name}.svg"
    return QIcon(str(path)) if path.is_file() else QIcon.fromTheme(f"zoom-{name}")


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


class ZoomableScrollArea(QScrollArea):
    """QScrollArea con zoom por Ctrl + rueda del ratón."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._zoom_callback = None

    def set_zoom_callback(self, callback) -> None:
        self._zoom_callback = callback

    def wheelEvent(self, event) -> None:
        if (event.modifiers() & Qt.KeyboardModifier.ControlModifier
                and self._zoom_callback is not None):
            delta = event.angleDelta().y()
            self._zoom_callback(1.25 if delta > 0 else 0.8)
            event.accept()
            return
        super().wheelEvent(event)


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
        self._row_pdfs: list[Path] = []
        self._preview_source_pixmap: QPixmap | None = None
        self._preview_zoom = 1.0  # 1.0 = ajustado a la altura disponible
        self._preprocessed_images: dict[tuple[str, int], QImage] = {}
        self._preprocessed_active = False
        self._log_sink = QtLogSink()
        self._processed_template: Template | None = None
        self._processed_dpi: int | None = None
        self._last_run_cancelled = False

        self._detected_dpi = 200
        self._detected_dpis: dict[str, int] = {}
        self._config: AppConfig | None = None
        self._ms_per_page = _load_ms_per_page()

        self._timer = QTimer(self)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._on_timer_tick)
        self._run_started: float | None = None
        self._done_global = 0
        self._total_global = 0
        self._page_deltas: deque[float] = deque(maxlen=8)
        self._last_done = 0
        self._last_page_at: float | None = None
        self._spinner_idx = 0
        self._spinner_active = False

        self._log_buffer: list[str] = []
        self._log_timer = QTimer(self)
        self._log_timer.setInterval(300)
        self._log_timer.timeout.connect(self._flush_log_buffer)
        self._log_timer.start()

        self._table_timer = QTimer(self)
        self._table_timer.setInterval(120)
        self._table_timer.timeout.connect(self._on_table_chunk)
        self._table_columns: list[str] = []
        self._table_pending: list = []
        self._table_important_field_ids: set[str] = set()
        self._selected_important_columns: set[str] = set()
        self._important_fields_user_selected = False
        self._csv_viewer: CsvViewerWindow | None = None

        self._preview_thread = QThread(self)
        self._preview_loader = PreviewLoader()
        self._preview_loader.moveToThread(self._preview_thread)
        self._preview_loader.requested.connect(self._preview_loader.run)
        self._preview_loader.previewReady.connect(self._on_preview_ready)
        self._preview_thread.start()
        self._preview_pending: tuple[int, str] | None = None
        self._preview_results: dict[tuple[str, int], object] = {}

        self._file_rows: dict[int, dict] = {}
        self._row_ms: dict[int, float] = {}
        self._row_started: dict[int, float] = {}
        self._current_file_index = 0
        self._file_page_counts: list[int] = []

        self._build_ui()
        self._attach_logger()
        self._refresh_templates()
        self._install_zoom_shortcuts()

    def load_initial_data(self) -> None:
        """Carga los datos del disco después de mostrar la ventana."""
        self._load_default_input()

    # ── UI ──────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        controls = self._build_controls()
        control_scroll = QScrollArea()
        control_scroll.setObjectName("controlScroll")
        control_scroll.setWidgetResizable(True)
        control_scroll.setFrameShape(QFrame.Shape.NoFrame)
        control_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        control_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        control_scroll.setWidget(controls)
        root.addWidget(control_scroll, 0)

        root.addLayout(self._build_progress_row())
        root.addWidget(self._build_splitter(), stretch=1)

        bottom = self._build_bottom_splitter()
        bottom.setMinimumHeight(100)
        root.addWidget(bottom)

    def _build_controls(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._build_input_group())
        layout.addWidget(self._build_process_group())
        layout.addWidget(self._build_options_group())
        layout.addWidget(self._build_advanced_panel())
        return panel

    def _build_input_group(self) -> QGroupBox:
        group = QGroupBox("Entrada")
        grid = QGridLayout(group)
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
        row.setSpacing(10)

        row.addWidget(QLabel("Motor:"))
        self.engine_combo = QComboBox()
        self.engine_combo.addItem("PaddleOCR (recomendado)", "paddle")
        self.engine_combo.addItem("Tesseract", "tesseract")
        self.engine_combo.setToolTip("Motor de reconocimiento de texto")
        row.addWidget(self.engine_combo)

        row.addWidget(QLabel("Motor fechas:"))
        self.date_engine_combo = QComboBox()
        self.date_engine_combo.addItem("(usar mismo)", "")
        self.date_engine_combo.addItem("PaddleOCR", "paddle")
        self.date_engine_combo.addItem("Tesseract", "tesseract")
        self.date_engine_combo.setToolTip(
            "Motor específico para campos de fecha (day/month/year)"
        )
        row.addWidget(self.date_engine_combo)

        row.addWidget(QLabel("Archivos:"))
        self.limit_spin = QSpinBox()
        self.limit_spin.setRange(0, 9999)
        self.limit_spin.setValue(0)
        self.limit_spin.setToolTip("Procesar solo los primeros N archivos (0 = todos)")
        self.limit_spin.valueChanged.connect(self._refresh_estimate)
        row.addWidget(self.limit_spin)

        row.addWidget(QLabel("Páginas:"))
        self.pages_spin = QSpinBox()
        self.pages_spin.setRange(0, 9999)
        self.pages_spin.setValue(0)
        self.pages_spin.setToolTip(
            "Procesar solo las primeras N páginas de cada archivo (0 = todas)"
        )
        self.pages_spin.valueChanged.connect(self._refresh_estimate)
        row.addWidget(self.pages_spin)

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
            "Mostrar únicamente matrícula, log_number, fecha y firmas "
            "obligatorias; oculta celdas auxiliares y campos opcionales."
        )
        self.important_fields_check.toggled.connect(self._on_fields_toggled)
        self.fields_check.toggled.connect(
            self.important_fields_check.setEnabled
        )
        info_row.addWidget(self.important_fields_check)
        self.important_fields_button = ImportantFieldsButton()
        self.important_fields_button.setToolTip("Seleccionar campos importantes")
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
        self.date_fallback_check = QCheckBox("OCR de respaldo para fechas")
        self.date_fallback_check.setChecked(True)
        self.date_fallback_check.setToolTip(
            "Reintenta la lectura de día/mes/año, matrícula y log_number con "
            "Tesseract restringido cuando la lectura principal de PaddleOCR "
            "no produce un valor válido."
        )
        check_row.addWidget(self.date_fallback_check)
        self.date_slot_check = QCheckBox("OCR por ranuras de casilla")
        self.date_slot_check.setChecked(True)
        self.date_slot_check.setToolTip(
            "Para las fechas: detecta las celdas entre las líneas verticales "
            "impresas y lee carácter por carácter (dígitos y mes) con "
            "restricciones. La lectura por posiciones verifica siempre el "
            "OCR global cuando la retícula está disponible."
        )
        check_row.addWidget(self.date_slot_check)
        check_row.addStretch()
        adv.addLayout(check_row)

        date_info = QLabel(
            "Fechas manuscritas: lectura DD|MMM|AA por retícula, sin VLM."
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
        """Muestra la distribución automática para los hilos seleccionados."""
        selected_threads = self.threads_spin.value()
        effective = self._effective_threads(selected_threads)
        selected_workers, selected_per_worker = recommended_parallelism(effective)
        automatic = (
            f"{selected_workers} worker(s) x {selected_per_worker} "
            f"hilo(s) = {effective} hilos"
        )
        if self._effective_threads(selected_threads) < selected_threads:
            automatic += (
                f" ({selected_threads - effective} reservado(s) para la interfaz)"
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
            "sin reprocesar los archivos"
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

        nav = QHBoxLayout()
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
        self.page_label = QLabel("Página 0/0")
        self.page_label.setMinimumWidth(90)
        self.preview_pdf_combo = QComboBox()
        self.preview_pdf_combo.setMinimumWidth(180)
        self.preview_pdf_combo.setMaximumWidth(280)
        self.preview_pdf_combo.setToolTip("PDF activo en la vista previa")
        self.preview_pdf_combo.currentIndexChanged.connect(
            self._on_preview_pdf_changed
        )
        self.page_edit = QLineEdit()
        self.page_edit.setPlaceholderText("Página")
        self.page_edit.setToolTip("Escriba un número de página y pulse Ir")
        self.page_edit.setFixedWidth(70)
        self.page_edit.returnPressed.connect(self._jump_to_page_number)
        page_go = QPushButton("Ir")
        page_go.setToolTip("Saltar a la página escrita")
        page_go.clicked.connect(self._jump_to_page_number)
        nav.addStretch()
        nav.addWidget(QLabel("PDF:"))
        nav.addWidget(self.preview_pdf_combo)
        nav.addWidget(self.btn_prev)
        nav.addWidget(self.page_label)
        nav.addWidget(self.page_edit)
        nav.addWidget(page_go)
        nav.addWidget(self.btn_next)
        nav.addStretch()

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
        self.btn_zoom_in.setIcon(_load_zoom_icon("in"))
        self.btn_zoom_in.setToolTip("Acercar la vista previa")
        self.btn_zoom_in.setAccessibleName("Acercar vista previa")
        self.btn_zoom_in.setIconSize(QSize(14, 14))
        self.btn_zoom_in.setFixedSize(28, 28)
        self.btn_zoom_in.clicked.connect(lambda: self._zoom_preview(1.25))
        zoom_panel.addWidget(self.btn_zoom_in, 0, Qt.AlignmentFlag.AlignHCenter)
        self.btn_zoom_fit = QToolButton()
        self.btn_zoom_fit.setObjectName("zoomControl")
        self.btn_zoom_fit.setIcon(_load_zoom_icon("fit"))
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
        self.btn_zoom_out.setIcon(_load_zoom_icon("out"))
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
        page_layout.addLayout(nav)
        preview_layout.addWidget(page_area, stretch=1)
        self._update_preview_zoom_controls()

        self.table = QTableWidget(0, 0)
        self.table.setAccessibleName("Resultados de validación")
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionsClickable(True)
        self.table.horizontalHeader().setHighlightSections(False)
        self.table.horizontalHeader().setSectionsMovable(False)
        self.table.horizontalHeader().setFixedHeight(30)
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
        self.log_view.setMaximumHeight(150)
        self.log_view.document().setMaximumBlockCount(2000)
        self.log_view.setMinimumWidth(340)
        splitter.addWidget(self.log_view)

        times = QWidget()
        times_layout = QVBoxLayout(times)
        times_layout.setSpacing(4)
        title = QLabel("Avance por archivo")
        title.setStyleSheet("font-weight: bold;")
        times_layout.addWidget(title)

        self.preview_context_label = QLabel("PDF 0 de 0 · Página 0 de 0")
        self.preview_context_label.setObjectName("previewContext")
        self.preview_context_label.setWordWrap(True)
        times_layout.addWidget(self.preview_context_label)

        self.times_vbox = QVBoxLayout()
        self.times_vbox.setContentsMargins(0, 0, 0, 0)
        self.times_vbox.setSpacing(2)
        self.times_container = QWidget()
        self.times_container.setLayout(self.times_vbox)

        self.times_scroll = QScrollArea()
        self.times_scroll.setWidgetResizable(True)
        self.times_scroll.setWidget(self.times_container)
        self.times_scroll.setMaximumHeight(150)
        times_layout.addWidget(self.times_scroll, 1)

        self.empty_times_label = QLabel("Sin archivos procesados aún.")
        times_layout.addWidget(self.empty_times_label)

        times.setMinimumWidth(440)
        times.setMaximumWidth(620)
        splitter.addWidget(times)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        return splitter

    def _ensure_file_rows(self, total: int) -> None:
        """Crea una fila por archivo en el panel de tiempos (idempotente)."""
        if total == 0 or len(self._file_rows) == total:
            return
        _clear_layout(self.times_vbox)
        self._file_rows = {}
        self._row_ms = {}
        self._row_started = {}
        for i in range(total):
            row = QHBoxLayout()
            row.setSpacing(6)
            name = QLabel("")
            name.setMinimumWidth(150)
            name.setMaximumWidth(220)
            bar = QProgressBar()
            bar.setObjectName("timeBar")
            bar.setFixedHeight(14)
            bar.setMinimumWidth(120)
            bar.setRange(0, 100)
            bar.setValue(0)
            bar.setTextVisible(True)
            secs = QLabel("–")
            secs.setMinimumWidth(64)
            secs.setAlignment(Qt.AlignmentFlag.AlignRight)
            row.addWidget(name)
            row.addWidget(bar, 1)
            row.addWidget(secs)
            self.times_vbox.addLayout(row)
            self._file_rows[i] = {"name": name, "bar": bar, "secs": secs}
        self.times_vbox.addStretch()

    def _rescale_time_bars(self) -> None:
        """Conservado por compatibilidad; el avance ya es porcentaje real."""
        for index in self._row_ms:
            row = self._file_rows.get(index)
            if row is not None:
                row["bar"].setRange(0, 100)
                row["bar"].setValue(100)

    def _set_file_page_counts(self, paths: list[Path]) -> None:
        from app.vision.pdf_loader import page_count

        limit = self.pages_spin.value()
        self._file_page_counts = []
        for path in paths:
            try:
                count = page_count(path)
            except Exception:  # noqa: BLE001 - el pipeline reportará el PDF inválido
                count = 0
            self._file_page_counts.append(min(count, limit) if limit > 0 else count)

    def _update_file_progress(self, done: int) -> None:
        """Distribuye el contador global en porcentajes por PDF."""
        if not self._file_page_counts or not self._file_rows:
            return
        offset = 0
        for index, total in enumerate(self._file_page_counts):
            row = self._file_rows.get(index)
            if row is None:
                offset += total
                continue
            if total <= 0:
                value = 100 if done >= offset else 0
            else:
                value = round(max(0, min(total, done - offset)) * 100 / total)
            row["bar"].setRange(0, 100)
            row["bar"].setValue(value)
            offset += total

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
        self._preprocessed_images = {}
        self._preprocessed_active = False
        seen: set[str] = set()
        for p in paths:
            p = Path(p)
            key = str(p.resolve())
            if p.exists() and p.suffix.lower() == ".pdf" and key not in seen:
                seen.add(key)
                self._pdf_paths.append(p)
        self._refresh_input_summary()
        self._set_preview_documents(self._pdf_paths)
        self._preview_selected_input()
        self._detect_dpi()
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
            self._set_preview_documents([])
            self._preview_zoom = 1.0
            self.preview_label.clear()
            self.preview_label.setText("Vista previa")
            self._update_preview_zoom_controls()
            self._update_preview_nav()
            return
        self._preview_source_pixmap = None
        self._preview_zoom = 1.0
        self.preview_label.clear()
        self.preview_label.setText("Cargando vista previa…")
        self._update_preview_zoom_controls()
        self._show_preview_page(1, self._pdf_paths[0])

    def _set_preview_documents(self, paths: list[Path]) -> None:
        """Actualiza el selector de PDF sin perder el documento activo."""
        unique: list[Path] = []
        seen: set[str] = set()
        for path in paths:
            path = Path(path)
            key = str(path.resolve()).casefold()
            if path.is_file() and path.suffix.lower() == ".pdf" and key not in seen:
                seen.add(key)
                unique.append(path)
        current = str(self._preview_pdf.resolve()).casefold() if self._preview_pdf else ""
        self._preview_documents = unique
        self.preview_pdf_combo.blockSignals(True)
        self.preview_pdf_combo.clear()
        for path in unique:
            self.preview_pdf_combo.addItem(path.name, str(path))
        index = next(
            (i for i, path in enumerate(unique)
             if str(path.resolve()).casefold() == current),
            0,
        )
        self.preview_pdf_combo.setCurrentIndex(index if unique else -1)
        self.preview_pdf_combo.setEnabled(bool(unique))
        self.preview_pdf_combo.blockSignals(False)
        self._update_preview_nav()

    def _on_preview_pdf_changed(self, index: int) -> None:
        if 0 <= index < len(self._preview_documents):
            self._preview_source_pixmap = None
            self._preview_zoom = 1.0
            self._show_preview_page(1, self._preview_documents[index])

    def _jump_to_page_number(self) -> None:
        if self._preview_pdf is None:
            return
        try:
            page = int(self.page_edit.text())
        except ValueError:
            self.page_edit.setText(str(self._preview_page))
            return
        self._show_preview_page(page, self._preview_pdf)

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
        selected = (
            self._selected_important_columns
            if self._important_fields_user_selected
            else self._default_important_columns(columns)
        )
        dialog = ImportantFieldsDialog(columns, selected, self)
        dialog.selectionChanged.connect(self._set_important_columns)
        dialog.exec()

    def _set_important_columns(self, columns: set[str]) -> None:
        self._important_fields_user_selected = True
        self._selected_important_columns = set(columns)
        self._apply_csv_table_view()

    def _columns_for_template_preview(self) -> list[str]:
        template = self._processed_template or self._load_template()
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
            "file", "page", "date", "time_ms", "dup", "log_number",
            "matricula", "flight_number", "pilot_signature",
            "captain_signature", "captain_license",
        }
        important.update(column for column in columns if column.endswith("_signature"))
        return set(columns).intersection(important)

    def _detect_dpi(self) -> None:
        from app.vision.pdf_loader import detect_dpi

        self._detected_dpis = {}
        for p in self._pdf_paths:
            try:
                detected = detect_dpi(p, default=600)
                self._detected_dpis[str(p.resolve())] = detected
            except Exception:  # noqa: BLE001 - PDF inválido, se sigue
                continue
        if self._pdf_paths:
            self._detected_dpi = self._detected_dpis.get(
                str(self._pdf_paths[0].resolve()), 200
            )

    def _refresh_input_summary(self) -> None:
        n = len(self._pdf_paths)
        if not n:
            self.input_edit.setText("")
            return
        names = ", ".join(p.name for p in self._pdf_paths)
        self.input_edit.setText(names)
        self.input_edit.setToolTip("\n".join(str(p) for p in self._pdf_paths))

    def _resolved_paths(self) -> list[Path]:
        """Aplica el límite 'primeros N archivos' a la entrada actual."""
        paths = list(self._pdf_paths)
        limit = self.limit_spin.value()
        if limit > 0:
            paths = paths[:limit]
        return paths

    def _total_pages_for(self, paths: list[Path]) -> int:
        from app.vision.pdf_loader import page_count

        pages = self.pages_spin.value()
        total = 0
        for p in paths:
            try:
                count = page_count(p)
            except Exception:  # noqa: BLE001 - archivo inválido
                continue
            total += min(count, pages) if pages > 0 else count
        return total

    def _refresh_estimate(self) -> None:
        resolved = self._resolved_paths()
        pages = self._total_pages_for(resolved)
        if pages and self._ms_per_page:
            seconds = pages * self._ms_per_page / 1000.0
            self.estimate_label.setText(
                f"Tiempo estimado: {_format_clock(seconds)}  "
                f"{pages} páginas  {len(resolved)} archivos"
            )
        elif resolved:
            self.estimate_label.setText("Estimación no disponible")
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
            CsvReporter().write(
                self._reports,
                csv_path,
                template,
                date_mode=self._csv_date_mode(),
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
        )

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
        engine = self.engine_combo.currentData() or "paddle"
        date_engine_name = self.date_engine_combo.currentData() or ""
        return AppConfig(
            dpi=200,
            deskew=self.deskew_check.isChecked(),
            align=self.align_check.isChecked(),
            ocr_engine=engine,
            ocr_lang="eng" if engine == "tesseract" else "en",
            date_engine_name=date_engine_name,
            remove_printed=self.remove_printed_check.isChecked(),
            crop_preprocess=self.crop_preprocess_check.isChecked(),
            date_ocr_fallback=self.date_fallback_check.isChecked(),
            date_slot_ocr=self.date_slot_check.isChecked(),
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

        resolved = self._resolved_paths()
        if not resolved:
            QMessageBox.warning(self, "Aviso", "No hay archivos para preprocesar.")
            return

        self._config = self._current_processing_config()
        self._preprocessed_images = {}
        self._preprocessed_active = False
        worker = PreprocessWorker(
            resolved,
            self._config,
            max_pages=self.pages_spin.value() or None,
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
        total = self._total_pages_for(resolved)
        self._set_file_page_counts(resolved)
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

        resolved = self._resolved_paths()
        if not resolved:
            QMessageBox.warning(self, "Aviso", "No hay archivos para procesar.")
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

        max_pages = self.pages_spin.value() or None
        selected_threads = self.threads_spin.value()
        effective_threads = self._effective_threads(selected_threads)
        workers, threads = recommended_parallelism(effective_threads)

        self._worker = PipelineWorker(
            resolved,
            Path(self.template_combo.currentData()),
            self._config,
            max_pages=max_pages,
            reference_page=self.ref_spin.value(),
            workers=workers,
            cpu_threads=threads,
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.file_started.connect(self._on_file_started)
        self._worker.file_finished.connect(self._on_file_finished)
        self._worker.succeeded.connect(self._on_succeeded)
        self._worker.failed.connect(self._on_failed)

        self.btn_process.setEnabled(False)
        self.btn_preprocess.setEnabled(False)
        self.btn_export.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.progress.setRange(0, max(1, self._total_pages_for(resolved)))
        self.progress.setValue(0)
        self.table.setRowCount(0)
        self.table.setColumnCount(0)
        self._update_duplicate_summary([])
        self.csv_columns_toggle.setEnabled(False)
        self.csv_columns_toggle.setVisible(False)
        self._table_timer.stop()
        self._table_pending = []
        self._clear_times()

        self._total_global = self._total_pages_for(resolved)
        self._set_file_page_counts(resolved)
        self._done_global = 0
        self._page_deltas.clear()
        self._last_done = 0
        self._last_page_at = None
        self._run_started = time.monotonic()
        self._spinner_active = True
        self._timer.start()
        self.status_label.setText("Procesando…")
        estimate = self._total_global * self._ms_per_page / 1000.0
        self._set_time_summary(0.0, estimate, estimate)

        logger.info(
            f"Iniciando procesamiento: {len(resolved)} archivo(s), "
            f"{self._total_global} página(s), 200 DPI base / "
            f"hasta 600 DPI en fechas por PDF, "
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
        self, pdf_path: str, page_number: int, image
    ) -> None:
        """Guarda una página preprocesada y la muestra si está seleccionada."""
        import cv2

        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        height, width, channels = rgb.shape
        qimage = QImage(
            rgb.data,
            width,
            height,
            channels * width,
            QImage.Format.Format_RGB888,
        ).copy()
        key = (pdf_path, page_number)
        self._preprocessed_images[key] = qimage
        if self._preview_pdf is not None and key == (
            str(self._preview_pdf),
            self._preview_page,
        ):
            self._set_preview_qimage(qimage)

    def _on_preprocess_succeeded(self, cancelled: bool) -> None:
        elapsed = (
            time.monotonic() - self._run_started
            if self._run_started is not None
            else None
        )
        self._preprocessed_active = bool(self._preprocessed_images)
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
        row["name"].setText(name)
        row["bar"].setRange(0, 100)
        row["bar"].setValue(0)
        row["secs"].setText("…")
        self._row_started[index - 1] = time.monotonic()
        self._current_file_index = index

    def _on_file_finished(self, index: int, report) -> None:
        """Cierra la fila del archivo con su tiempo real y normaliza."""
        row = self._file_rows.get(index - 1)
        if row is not None:
            font = row["name"].font()
            font.setBold(False)
            row["name"].setFont(font)
            self._row_ms[index - 1] = report.processing_ms
            row["bar"].setRange(0, 100)
            row["bar"].setValue(100)
            row["secs"].setText(_format_clock(report.processing_ms / 1000.0))
            self._rescale_time_bars()
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
        # Estimación en vivo: ritmo = mezcla de lo aprendido (última corrida,
        # ya incluye calibración + VLM) con la mediana de los deltas reales
        # entre páginas completadas. La mediana ignora páginas lentas atípicas
        # y el total converge al mismo número que reporta el log final.
        if self._total_global > 0:
            pending = self._total_global - self._done_global
            remaining = max(0.0, pending * self._ms_per_page / 1000.0)
            if pending > 0:
                rate = self._ms_per_page
                if self._page_deltas:
                    live = median(self._page_deltas)
                    weight = min(1.0, self._done_global / 8.0)
                    rate = (1.0 - weight) * rate + weight * live
                remaining = max(0.0, pending * rate / 1000.0)
            total = elapsed + remaining
        self._set_time_summary(elapsed, remaining, total)
        # Reloj en vivo de la fila del archivo en curso.
        row = self._file_rows.get(self._current_file_index - 1)
        if row is not None and self._current_file_index:
            started = self._row_started.get(self._current_file_index - 1)
            if started is not None:
                row["secs"].setText(_format_clock(time.monotonic() - started))

    def _on_progress(self, done: int, total: int, message: str) -> None:
        if total > 0:
            if self.progress.maximum() != total:
                self.progress.setRange(0, total)
            self.progress.setValue(done)
            # Cada incremento de ``done`` es una página real completada (la
            # calibración anuncia etapas con done=0 y no cuenta). Los deltas
            # entre eventos alimentan el ritmo medido del estimador.
            if done > self._last_done:
                now = time.monotonic()
                if self._last_done > 0 and self._last_page_at is not None:
                    self._page_deltas.append((now - self._last_page_at) * 1000.0)
                self._last_page_at = now
                self._last_done = done
            self._done_global = done
            self._update_file_progress(done)
        self.status_label.setText(message)

    def _on_succeeded(self, reports: list[ValidationReport]) -> None:
        elapsed = (
            time.monotonic() - self._run_started
            if self._run_started is not None
            else None
        )
        self._timer.stop()
        self._reports = reports
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
        self._update_performance(reports)
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
        total_ms = sum(r.processing_ms for r in reports)
        calib_ms = sum(r.calibration_ms for r in reports)
        summary = (
            f"OK: {ok} | WARNING: {warn} | ERROR: {err} | "
            f"En blanco: {blank} | "
            f"Calibración: {calib_ms / 1000:.2f} s + "
            f"Procesado: {(total_ms - calib_ms) / 1000:.2f} s = "
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
            vlm_stats=getattr(self._worker, "vlm_stats", []),
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

    def _update_performance(self, reports: list[ValidationReport]) -> None:
        """Aprende el costo por página de la corrida para futuras estimas."""
        pages = sum(len(r.pages) for r in reports)
        if pages > 0:
            self._ms_per_page = max(1.0, sum(r.processing_ms for r in reports) / pages)
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
        con ``_table_timer`` se insertan ``_TABLE_CHUNK`` filas por tick.
        """
        self.table.setUpdatesEnabled(False)
        try:
            self.table.setRowCount(0)
            self._row_pdfs = []
            reporter = CsvReporter()
            fields = reporter.fields_for(reports, self._processed_template)
            columns = reporter.columns_for(reports, self._processed_template)
            duplicates = detect_duplicate_log_pages(reports)
            self._update_duplicate_summary(duplicates)
            duplicate_iter = iter(duplicates)
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
            if not self._important_fields_user_selected:
                self._selected_important_columns = self._default_important_columns(columns)
            else:
                self._selected_important_columns.intersection_update(columns)
            self.csv_columns_toggle.setEnabled(bool(columns))
            self.csv_columns_toggle.setVisible(bool(columns))
            self._apply_csv_table_view()
            self._table_pending = pending
            if pending:
                self.btn_prev.setEnabled(False)
                self.btn_next.setEnabled(False)
                self._table_timer.start()
            else:
                self.table.setSortingEnabled(True)
        finally:
            self.table.setUpdatesEnabled(True)

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

    def _on_table_chunk(self) -> None:
        if not self._table_pending:
            self._table_timer.stop()
            if self._outputs_worker is not None and self._outputs_worker.isRunning():
                self.status_label.setText("Generando salidas…")
            self._update_preview_nav()
            self.table.viewport().update()
            return
        batch = self._table_pending[:_TABLE_CHUNK]
        del self._table_pending[:_TABLE_CHUNK]
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
                if column == _DUP_COLUMN:
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
            self.table.setSortingEnabled(True)

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
        """Normalización final del panel: barras relativas al máximo del lote."""
        _clear_layout(self.times_vbox)
        self._file_rows = {}
        self._row_ms = {}
        self._row_started = {}
        self._current_file_index = 0
        self.empty_times_label.setVisible(not bool(reports))
        if not reports:
            return
        for report in reports:
            row = QHBoxLayout()
            row.setSpacing(6)
            name = QLabel(Path(report.pdf_path).name)
            name.setToolTip(str(report.pdf_path))
            name.setMinimumWidth(150)
            name.setMaximumWidth(220)
            bar = QProgressBar()
            bar.setObjectName("timeBar")
            bar.setFixedHeight(14)
            bar.setMinimumWidth(120)
            bar.setRange(0, 100)
            bar.setValue(100)
            bar.setTextVisible(True)
            seconds = QLabel(_format_clock(report.processing_ms / 1000.0))
            seconds.setAlignment(Qt.AlignmentFlag.AlignRight)
            seconds.setMinimumWidth(64)
            row.addWidget(name)
            row.addWidget(bar, 1)
            row.addWidget(seconds)
            self.times_vbox.addLayout(row)
        self.times_vbox.addStretch()

    # ── Vista previa ───────────────────────────────────────────────────

    def _refresh_preview_template(self, _index: int = -1) -> None:
        """Redibuja las casillas de la plantilla sobre la vista actual."""
        if self._preview_pdf is not None:
            self._show_preview_page(self._preview_page, self._preview_pdf)

    def _on_fields_toggled(self, _checked: bool) -> None:
        if self._preview_pdf is not None:
            self._show_preview_page(self._preview_page, self._preview_pdf)

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
        """Habilita/deshabilita las flechas según la página actual."""
        has_pdf = self._preview_pdf is not None
        self.btn_prev.setEnabled(has_pdf and self._preview_page > 1)
        self.btn_next.setEnabled(
            has_pdf
            and self._preview_total > 0
            and self._preview_page < self._preview_total
        )
        self.page_edit.setText(str(self._preview_page) if has_pdf else "")
        index = next(
            (i for i, path in enumerate(self._preview_documents)
             if self._preview_pdf is not None
             and str(path.resolve()).casefold()
             == str(self._preview_pdf.resolve()).casefold()),
            -1,
        )
        self.preview_pdf_combo.blockSignals(True)
        self.preview_pdf_combo.setCurrentIndex(index)
        self.preview_pdf_combo.blockSignals(False)
        pdf_text = self._preview_pdf.name if has_pdf else "Sin PDF"
        pdf_position = f"PDF {index + 1} de {len(self._preview_documents)}"
        context = (
            f"{pdf_position} · {pdf_text} · Página "
            f"{self._preview_page if has_pdf else 0} de "
            f"{self._preview_total if has_pdf else 0}"
        )
        self.preview_context_label.setText(context)
        self.page_label.setText(
            f"Página {self._preview_page}/{self._preview_total or '?'}"
            if has_pdf else "Página 0/0"
        )

    def _prev_page(self) -> None:
        if self._preview_pdf and self._preview_page > 1:
            self._show_preview_page(self._preview_page - 1, self._preview_pdf)

    def _next_page(self) -> None:
        if (
            self._preview_pdf
            and self._preview_total
            and self._preview_page < self._preview_total
        ):
            self._show_preview_page(self._preview_page + 1, self._preview_pdf)

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
        for field in _visible_preview_fields(template, important_only):
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
        cached = self._preprocessed_images.get((str(pdf_path), page_number))
        # Tras procesar, la geometría guardada en PageResult refleja exactamente
        # la alineación (incluido el anclaje por lote) usada por el OCR. Una
        # imagen preprocesada con anterioridad puede haber usado otro anclaje,
        # así que solo reutilizamos esa caché mientras aún no hay resultado.
        if (
            self._preprocessed_active
            and cached is not None
            and self._current_preview_result() is None
        ):
            self._set_preview_qimage(cached)
            return
        self._preview_loader.requested.emit(
            page_number, str(pdf_path), self._current_preview_geometry()
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
        """Carga una imagen fuente y le aplica el overlay actual."""
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

    def resizeEvent(self, event) -> None:
        """Reajusta la vista también cuando cambia el tamaño de la ventana."""
        super().resizeEvent(event)
        if self._preview_source_pixmap is not None:
            QTimer.singleShot(0, self._render_preview_pixmap)

    def closeEvent(self, event) -> None:
        self._timer.stop()
        self._table_timer.stop()
        self._preview_thread.quit()
        self._preview_thread.wait(2000)
        super().closeEvent(event)


def _color_for(status: Status):
    from PySide6.QtGui import QColor

    return QColor(_COLORS[status])

"""Widgets Qt compartidos por las ventanas de la aplicación."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QRectF, QSize, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QIcon,
    QPainter,
    QPainterPath,
    QPalette,
    QRegion,
)
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStyle,
    QStyledItemDelegate,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

_ASSETS = Path(__file__).resolve().parents[2] / "assets"
_DROPDOWN_ARROW = (_ASSETS / "dropdown_arrow.svg").as_posix()

# Grises oscuros de las tablas de datos: el mismo rgb(49, 49, 49) del botón
# principal y del panel de tiempos, para que la tabla pertenezca a la
# aplicación en lugar de ser un recuadro blanco pegado sobre ella. La cabecera
# usa el tono pulsado del botón y las filas alternas suben un escalón para que
# se distingan sin romper el bloque.
TABLE_BASE_BG = "#313131"  # rgb(49, 49, 49)
TABLE_ALTERNATE_BG = "#383838"
TABLE_HEADER_BG = "#262626"  # rgb(38, 38, 38)
TABLE_GRID = "#4a4a4a"
TABLE_TEXT = "#ffffff"
TABLE_SELECTION_BG = "#2f81f7"
# La fila bajo el cursor y la fila marcada con su casilla. Las dos son azules
# porque las dos hablan de lo mismo que la selección, y las dos quedan por
# debajo de ella: el cursor solo pasa por encima y una marca no es lo que se
# está mirando, así que ninguna puede competir con la banda de la selección.
TABLE_HOVER_BG = "#3a4a5f"
TABLE_CHECKED_BG = "#1f4a7a"

# El visor de PDF acompaña a la tabla dentro de la misma ventana, así que va
# en su mismo gris. La superficie que rodea la página baja al tono más oscuro:
# el papel del escaneo es blanco y necesita flotar sobre algo, como en
# cualquier lector de PDF.
PANE_BG = TABLE_BASE_BG
PANE_SURFACE_BG = "#262626"
PANE_CONTROL_BG = "#3d3d3d"
PANE_CONTROL_HOVER = "#4a4a4a"
PANE_BORDER = "#4a4a4a"
PANE_TEXT = TABLE_TEXT
# Los estados se leen como texto sobre el gris oscuro, no como relleno de
# celda: los tonos de la tabla (#1a7f37, #9a6700, #cf222e) están pensados para
# llevar texto blanco encima y sobre el panel quedarían casi invisibles.
PANE_STATUS_COLORS = {
    "OK": "#3fb950",
    "WARNING": "#d29922",
    "ERROR": "#f85149",
}

# Radio de esquina de la aplicación: el mismo de los QGroupBox, del panel de
# tiempos y del visor de PDF incrustado. La tabla es un cuadro más y no puede
# ser el único con las esquinas en pico.
TABLE_RADIUS = 6

# Ambas ventanas comparten esta hoja para que la tabla se vea igual en las dos.
DATA_TABLE_QSS = (
    "QTableView, QTableWidget {"
    f" background-color: {TABLE_BASE_BG};"
    f" alternate-background-color: {TABLE_ALTERNATE_BG};"
    f" color: {TABLE_TEXT};"
    f" gridline-color: {TABLE_GRID};"
    f" selection-background-color: {TABLE_SELECTION_BG};"
    f" selection-color: {TABLE_TEXT};"
    f" border: 1px solid {PANE_BORDER};"
    f" border-radius: {TABLE_RADIUS}px; }}"
    "QHeaderView { background-color: transparent; }"
    "QHeaderView::section {"
    f" background-color: {TABLE_HEADER_BG};"
    f" color: {TABLE_TEXT}; padding: 6px 8px; font-weight: 600;"
    f" border: 0; border-right: 1px solid {TABLE_GRID};"
    f" border-bottom: 1px solid {TABLE_GRID}; }}"
    "QTableCornerButton::section {"
    f" background-color: {TABLE_HEADER_BG};"
    f" border: 0; border-right: 1px solid {TABLE_GRID};"
    f" border-bottom: 1px solid {TABLE_GRID}; }}"
    # El hueco entre las dos barras de desplazamiento lo pinta la propia área
    # de scroll y es lo único que no respeta el radio: sin dejarlo
    # transparente, la esquina inferior derecha se queda en pico.
    "QTableView::corner, QTableWidget::corner { background: transparent; }"
)

# Las barras de desplazamiento son parte de la superficie: dejarlas en el
# blanco nativo ponía una franja luminosa al borde de cada widget oscuro. Se
# aplican por descendencia para que sirvan igual a la tabla y al visor de PDF.
def scrollbars_qss(scope: str) -> str:  # noqa: E302 - va junto a la hoja
    """Barras de desplazamiento oscuras para los widgets de ``scope``."""
    return (
        f"{scope} QScrollBar:vertical, {scope} QScrollBar:horizontal {{"
        f" background: {TABLE_HEADER_BG}; border: 0; margin: 0; }}"
        f"{scope} QScrollBar:vertical {{ width: 12px; }}"
        f"{scope} QScrollBar:horizontal {{ height: 12px; }}"
        f"{scope} QScrollBar::handle:vertical,"
        f"{scope} QScrollBar::handle:horizontal {{"
        f" background: {TABLE_GRID}; border-radius: 6px; margin: 2px; }}"
        f"{scope} QScrollBar::handle:vertical:hover,"
        f"{scope} QScrollBar::handle:horizontal:hover {{ background: #5f5f5f; }}"
        f"{scope} QScrollBar::handle:vertical {{ min-height: 24px; }}"
        f"{scope} QScrollBar::handle:horizontal {{ min-width: 24px; }}"
        # Sin esto Qt reserva el hueco de los botones de flecha y deja dos
        # cuadros vacíos en los extremos.
        f"{scope} QScrollBar::add-line, {scope} QScrollBar::sub-line {{"
        " width: 0; height: 0; border: 0; background: none; }"
        f"{scope} QScrollBar::add-page, {scope} QScrollBar::sub-page {{"
        " background: none; }"
    )


DATA_TABLE_QSS += scrollbars_qss("QTableView") + scrollbars_qss("QTableWidget")

# Recuadro flotante de zoom: el mismo bloque en la vista previa de la ventana
# principal y en el visor de PDF del visor de CSV. Vive aquí para que los dos
# no puedan separarse; cada ventana lo añade al final de su hoja, después de
# sus reglas de panel, para ganar a las que tienen la misma especificidad.
ZOOM_OVERLAY_QSS = """
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
"""

# Tipografia y controles de la aplicacion. Los colores salen de las mismas
# superficies oscuras de las tablas y visores para que todas las ventanas se
# lean como una sola aplicacion. El marco del sistema lo completa theme.py.
APP_CHROME_QSS = f"""
QMainWindow, QDialog {{
    background-color: {PANE_SURFACE_BG};
}}
QWidget {{
    color: {PANE_TEXT};
    font-family: "Segoe UI", "Segoe UI Variable Text", sans-serif;
    font-size: 10pt;
}}
QLabel:disabled, QCheckBox:disabled, QRadioButton:disabled {{
    color: #8c959f;
}}
QPushButton {{
    min-height: 22px;
    max-height: 22px;
    padding: 3px 10px;
    color: {PANE_TEXT};
    background-color: {PANE_CONTROL_BG};
    border: 1px solid {PANE_BORDER};
    border-radius: {TABLE_RADIUS}px;
}}
QToolButton {{
    padding: 2px 6px;
    color: {PANE_TEXT};
    background-color: {PANE_CONTROL_BG};
    border: 1px solid {PANE_BORDER};
    border-radius: {TABLE_RADIUS}px;
}}
QToolButton#primaryButton {{
    min-height: 20px;
    padding: 3px 10px;
}}
QToolButton[menuRole="dropdown"] {{
    min-height: 24px;
    max-height: 24px;
    padding: 2px 22px 2px 10px;
}}
QToolButton[menuRole="dropdown"]::menu-indicator {{
    subcontrol-origin: border;
    subcontrol-position: right center;
    position: relative;
    right: 7px;
    width: 10px;
    height: 6px;
    image: url("{_DROPDOWN_ARROW}");
}}
QToolButton[menuRole="split"] {{
    min-height: 22px;
    max-height: 22px;
    padding: 2px 30px 2px 8px;
}}
QToolButton[menuRole="split"]::menu-button {{
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 24px;
    border: 0;
    border-left: 1px solid {PANE_BORDER};
    border-top-right-radius: {TABLE_RADIUS}px;
    border-bottom-right-radius: {TABLE_RADIUS}px;
}}
QToolButton[menuRole="split"]::menu-button:hover {{
    background-color: {PANE_CONTROL_HOVER};
}}
QToolButton[menuRole="split"]::menu-arrow {{
    width: 10px;
    height: 6px;
    image: url("{_DROPDOWN_ARROW}");
}}
QToolButton#primaryButton[menuRole="split"] {{
    min-height: 22px;
    max-height: 22px;
}}
QPushButton:hover, QToolButton:hover {{
    background-color: {PANE_CONTROL_HOVER};
}}
QPushButton:pressed, QToolButton:pressed,
QPushButton:checked, QToolButton:checked {{
    background-color: {PANE_SURFACE_BG};
}}
QPushButton:focus, QToolButton:focus {{
    border-color: {TABLE_SELECTION_BG};
}}
QPushButton:default {{
    border-color: {TABLE_SELECTION_BG};
}}
QPushButton:disabled, QToolButton:disabled {{
    color: #8c959f;
    background-color: {TABLE_HEADER_BG};
    border-color: {PANE_BG};
}}
#primaryButton {{
    background-color: {TABLE_BASE_BG};
    color: {PANE_TEXT};
}}
#primaryButton:hover {{
    background-color: {PANE_CONTROL_HOVER};
}}
#primaryButton:pressed {{
    background-color: {TABLE_HEADER_BG};
}}
QToolButton#spinStepButton {{
    min-width: 18px; max-width: 18px; min-height: 0;
    padding: 0;
    border: 1px solid {PANE_BORDER};
    border-radius: {TABLE_RADIUS}px;
    background-color: {PANE_CONTROL_BG};
}}
QToolButton#spinStepButton:hover {{
    background-color: {PANE_CONTROL_HOVER};
    border-color: #8c959f;
}}
QToolButton#spinStepButton:pressed {{
    background-color: {PANE_SURFACE_BG};
    border-color: #8c959f;
}}
QToolButton#spinStepButton:disabled {{
    background-color: {TABLE_HEADER_BG};
    color: #8c959f;
}}
QLineEdit, QComboBox,
QDateEdit, QTimeEdit, QDateTimeEdit {{
    min-height: 21px;
    max-height: 21px;
    padding: 3px;
    color: {PANE_TEXT};
    background-color: {PANE_CONTROL_BG};
    border: 1px solid {PANE_BORDER};
    border-bottom: 2px solid #8c959f;
    border-radius: {TABLE_RADIUS}px;
    selection-background-color: {TABLE_SELECTION_BG};
    selection-color: {PANE_TEXT};
}}
QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover,
QDateEdit:hover, QTimeEdit:hover, QDateTimeEdit:hover {{
    background-color: {PANE_CONTROL_HOVER};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus,
QDateEdit:focus, QTimeEdit:focus, QDateTimeEdit:focus {{
    border-bottom-color: {TABLE_SELECTION_BG};
}}
QLineEdit:read-only {{
    background-color: {PANE_CONTROL_BG};
}}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled,
QComboBox:disabled, QDateEdit:disabled, QTimeEdit:disabled,
QDateTimeEdit:disabled {{
    color: #8c959f;
    background-color: {TABLE_HEADER_BG};
    border-color: {PANE_BG};
}}
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 28px;
    border: 0;
    border-left: 1px solid transparent;
    background: transparent;
}}
QSpinBox, QDoubleSpinBox {{
    min-height: 24px;
    max-height: 24px;
    padding-left: 3px;
    padding-right: 3px;
    padding-top: 0;
    padding-bottom: 0;
}}
QComboBox {{
    padding: 3px 30px 3px 8px;
}}
QComboBox::down-arrow {{
    width: 10px;
    height: 6px;
    image: url("{_DROPDOWN_ARROW}");
}}
QComboBox:hover::drop-down, QComboBox:on::drop-down {{
    background-color: {PANE_CONTROL_HOVER};
    border-left-color: {PANE_BORDER};
}}
QComboBox QAbstractItemView {{
    color: {PANE_TEXT};
    background-color: {PANE_BG};
    border: 1px solid {PANE_BORDER};
    border-radius: {TABLE_RADIUS}px;
    padding: 4px;
    selection-background-color: {TABLE_SELECTION_BG};
    outline: 0;
}}
QComboBox QAbstractItemView::item {{
    min-height: 26px;
    padding: 3px 8px;
    border-radius: 4px;
}}
QGroupBox {{
    color: {PANE_TEXT};
    background-color: {TABLE_BASE_BG};
    font-weight: 600;
    border: 1px solid {PANE_BORDER};
    border-radius: {TABLE_RADIUS}px;
    margin-top: 0;
    padding: 22px 8px 6px 8px;
}}
QGroupBox::title {{
    subcontrol-origin: border;
    subcontrol-position: top left;
    left: 8px;
    top: 5px;
    padding: 0;
    color: {PANE_TEXT};
    background: transparent;
}}
QCheckBox, QRadioButton {{ spacing: 6px; }}
QProgressBar {{
    min-height: 28px;
    max-height: 28px;
    color: {PANE_TEXT};
    background-color: {PANE_CONTROL_BG};
    border: 1px solid {PANE_BORDER};
    border-radius: {TABLE_RADIUS}px;
    text-align: center;
}}
QProgressBar::chunk {{
    background-color: {TABLE_SELECTION_BG};
    border-radius: 5px;
}}
QPlainTextEdit, QTextEdit, QListView, QListWidget, QTreeView, QTreeWidget {{
    color: {PANE_TEXT};
    background-color: {PANE_BG};
    alternate-background-color: {TABLE_ALTERNATE_BG};
    border: 1px solid {PANE_BORDER};
    border-radius: {TABLE_RADIUS}px;
    selection-background-color: {TABLE_SELECTION_BG};
    selection-color: {PANE_TEXT};
    outline: 0;
}}
QToolBar {{
    color: {PANE_TEXT};
    background-color: {PANE_SURFACE_BG};
    border: 0;
    border-bottom: 1px solid {PANE_BORDER};
    spacing: 4px;
    padding: 4px;
}}
QToolBar::separator {{
    width: 1px;
    margin: 5px 4px;
    background-color: {PANE_BORDER};
}}
QMenu {{
    color: {PANE_TEXT};
    background-color: {PANE_BG};
    border: 1px solid {PANE_BORDER};
    border-radius: {TABLE_RADIUS}px;
    padding: 4px;
}}
QMenu::item {{
    border-radius: {TABLE_RADIUS}px;
    min-height: 22px;
    padding: 5px 28px 5px 30px;
}}
QMenu::item:selected {{ background-color: {PANE_CONTROL_HOVER}; }}
QMenu::item:disabled {{ color: #8c959f; }}
QMenu::indicator {{
    width: 16px;
    height: 16px;
}}
QMenu::separator {{
    height: 1px;
    margin: 4px 8px;
    background-color: {PANE_BORDER};
}}
QTabWidget::pane {{
    border: 1px solid {PANE_BORDER};
    border-radius: {TABLE_RADIUS}px;
    background-color: {TABLE_BASE_BG};
}}
QTabBar::tab {{
    color: {PANE_TEXT};
    background-color: transparent;
    border: 0;
    border-radius: {TABLE_RADIUS}px;
    padding: 6px 12px;
}}
QTabBar::tab:hover {{ background-color: {PANE_CONTROL_HOVER}; }}
QTabBar::tab:selected {{
    background-color: {PANE_CONTROL_BG};
    border-bottom: 2px solid {TABLE_SELECTION_BG};
}}
QSplitter::handle {{ background-color: {PANE_SURFACE_BG}; }}
QSplitter::handle:hover {{ background-color: {PANE_BORDER}; }}
QScrollBar:vertical, QScrollBar:horizontal {{
    background: {TABLE_HEADER_BG};
    border: 0;
    margin: 0;
}}
QScrollBar:vertical {{ width: 12px; }}
QScrollBar:horizontal {{ height: 12px; }}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
    background: {PANE_BORDER};
    border-radius: 6px;
    margin: 2px;
}}
QScrollBar::handle:vertical:hover,
QScrollBar::handle:horizontal:hover {{ background: #5f5f5f; }}
QScrollBar::handle:vertical {{ min-height: 24px; }}
QScrollBar::handle:horizontal {{ min-width: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{
    width: 0;
    height: 0;
    border: 0;
    background: none;
}}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
QToolTip {{
    color: {PANE_TEXT};
    background-color: {PANE_CONTROL_BG};
    border: 1px solid {PANE_BORDER};
    border-radius: {TABLE_RADIUS}px;
    padding: 4px 7px;
}}
QStatusBar {{
    color: {PANE_TEXT};
    background-color: {PANE_SURFACE_BG};
}}
""" + ZOOM_OVERLAY_QSS


_APPLICATION_THEME_PROPERTY = "bitsApplicationThemeInstalled"


def window_stylesheet(local_qss: str) -> str:
    """Compone una hoja local sin duplicar el tema global instalado."""
    app = QApplication.instance()
    if app is not None and app.property(_APPLICATION_THEME_PROPERTY):
        return local_qss
    return APP_CHROME_QSS + local_qss


class MultiSelectMenu(QMenu):
    """Menu de casillas que no se cierra entre selecciones."""

    def _trigger_active_check(self) -> bool:
        action = self.activeAction()
        if action is None or not action.isEnabled() or not action.isCheckable():
            return False
        action.trigger()
        return True

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - API Qt
        if self._trigger_active_check():
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - API Qt
        if event.key() in (
            Qt.Key.Key_Space,
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
        ) and self._trigger_active_check():
            return
        super().keyPressEvent(event)


class SpinBoxWithButtons(QWidget):
    """Campo numérico con las flechas fuera del área de texto.

    El ``QSpinBox`` sigue siendo el dato público para no duplicar su API. Este
    contenedor solo separa sus dos pasos en una columna a la derecha y refleja
    tanto los límites como el estado habilitado del campo.
    """

    def __init__(self, spin: QSpinBox, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.spin = spin
        self.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Fixed,
        )
        spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(3)
        row.addWidget(spin)

        self.up_button = self._button(Qt.ArrowType.UpArrow, "Aumentar valor")
        self.down_button = self._button(
            Qt.ArrowType.DownArrow, "Disminuir valor"
        )
        # Comparten la fila para conservar exactamente el alto del campo.
        # Apilarlas duplicaría la altura de los controles compactos.
        row.addWidget(self.up_button)
        row.addWidget(self.down_button)

        self.up_button.clicked.connect(spin.stepUp)
        self.down_button.clicked.connect(spin.stepDown)
        spin.valueChanged.connect(self.sync_buttons)
        spin.installEventFilter(self)
        self.sync_buttons()

    def _button(self, arrow: Qt.ArrowType, accessible_name: str) -> QToolButton:
        button = QToolButton(self)
        button.setObjectName("spinStepButton")
        button.setArrowType(arrow)
        button.setAccessibleName(accessible_name)
        button.setToolTip(accessible_name)
        button.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Ignored
        )
        button.setAutoRepeat(True)
        button.setAutoRepeatDelay(300)
        button.setAutoRepeatInterval(80)
        return button

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - API Qt
        if watched is self.spin and event.type() == QEvent.Type.EnabledChange:
            self.sync_buttons()
        return super().eventFilter(watched, event)

    def sync_buttons(self, *_args) -> None:
        """Habilita cada flecha solo cuando ese paso se puede ejecutar."""
        enabled = self.spin.isEnabled() and not self.spin.isReadOnly()
        steps = self.spin.stepEnabled()
        self.up_button.setEnabled(
            enabled
            and bool(steps & QAbstractSpinBox.StepEnabledFlag.StepUpEnabled)
        )
        self.down_button.setEnabled(
            enabled
            and bool(steps & QAbstractSpinBox.StepEnabledFlag.StepDownEnabled)
        )


def _mezclado(fondo: QBrush, color: str, peso: float = 0.55) -> QColor:
    """Tiñe el color que ya tenía la celda en lugar de taparlo.

    El realce del cursor es pasajero y recorre la tabla entera: si borrara
    los colores de estado, media tabla cambiaría de significado mientras se
    mueve el ratón. Mezclándolo, la fila se lee como una sola banda y cada
    celda conserva de qué color era.
    """
    encima = QColor(color)
    if fondo.style() == Qt.BrushStyle.NoBrush:
        return encima
    debajo = fondo.color()
    resto = 1.0 - peso
    return QColor(
        round(debajo.red() * resto + encima.red() * peso),
        round(debajo.green() * resto + encima.green() * peso),
        round(debajo.blue() * resto + encima.blue() * peso),
    )


class _HoverRowTracker(QObject):
    """Recuerda sobre qué fila está el cursor y repinta las dos afectadas.

    Qt solo avisa del hover celda a celda, y ninguna celda sabe que su
    vecina de la misma fila también tiene que resaltarse. Aquí se guarda la
    fila en la propia vista (``hoverRow``), donde el delegado la consulta al
    pintar cada celda, y se repinta la fila que se deja y la que se toma.
    """

    def __init__(self, view: QAbstractItemView) -> None:
        super().__init__(view)
        self._view = view
        view.setProperty("hoverRow", -1)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - API Qt
        tipo = event.type()
        if tipo == QEvent.Type.MouseMove:
            self._marcar(self._view.indexAt(event.position().toPoint()).row())
        elif tipo in (QEvent.Type.Leave, QEvent.Type.Wheel):
            # Al salir no llega ningún movimiento más, y al rodar la rueda la
            # fila bajo el cursor cambia sin que el ratón se mueva.
            self._marcar(-1)
        return False

    def _marcar(self, fila: int) -> None:
        anterior = self._view.property("hoverRow")
        anterior = -1 if anterior is None else int(anterior)
        if fila == anterior:
            return
        self._view.setProperty("hoverRow", fila)
        for numero in (anterior, fila):
            if numero >= 0:
                self._repintar(numero)

    def _repintar(self, fila: int) -> None:
        modelo = self._view.model()
        if modelo is None or fila >= modelo.rowCount():
            return
        viewport = self._view.viewport()
        primera = self._view.visualRect(modelo.index(fila, 0))
        ultima = self._view.visualRect(
            modelo.index(fila, max(0, modelo.columnCount() - 1))
        )
        viewport.update(
            primera.united(ultima).adjusted(0, 0, viewport.width(), 0)
        )


class FlatSelectionDelegate(QStyledItemDelegate):
    """Pinta la fila seleccionada como una sola banda azul.

    El estilo nativo de Windows 11 dibuja la selección como un rectángulo
    redondeado *por celda*, con margen a los lados: una fila seleccionada se
    veía como una hilera de cuadros azules sueltos en vez de una selección
    continua. Aquí se le quita a la celda el estado de selección y el azul se
    pasa como fondo del ítem, que el estilo rellena a ras del rectángulo, tal
    como ya rellena los colores de estado de la tabla. La fila queda entonces
    como una banda de un solo color, sin esquinas redondas ni huecos.

    Quien decide si hay que pintar es la selección de la vista, no el estado
    que llegue en la celda. En una tabla que selecciona por filas las dos
    cosas deberían coincidir, pero basta que una celda no reciba ese estado
    (porque no tiene ítem, porque el estilo la trata aparte) para que la
    banda salga cortada justo ahí.
    """

    def _fila_seleccionada(self, index) -> bool:
        vista = self.parent()
        seleccion = getattr(vista, "selectionModel", None)
        if not callable(seleccion):
            return False
        modelo = seleccion()
        if modelo is None or vista.model() is not index.model():
            return False
        if (
            vista.selectionBehavior()
            is QAbstractItemView.SelectionBehavior.SelectItems
        ):
            return modelo.isSelected(index)
        return modelo.isRowSelected(index.row(), index.parent())

    def _fila_marcada(self, index) -> bool:
        """Si la fila lleva marcada su casilla, mire donde mire el cursor.

        La casilla vive en una sola columna, así que la vista dice en cuál
        con la propiedad ``checkColumn``; sin ella (una tabla sin casillas)
        no hay nada que pintar.
        """
        vista = self.parent()
        columna = vista.property("checkColumn") if vista is not None else None
        if columna is None or int(columna) < 0:
            return False
        modelo = index.model()
        if modelo is None or int(columna) >= modelo.columnCount():
            return False
        estado = modelo.index(index.row(), int(columna), index.parent()).data(
            Qt.ItemDataRole.CheckStateRole
        )
        return Qt.CheckState(estado) is Qt.CheckState.Checked if estado is not None else False

    def _fila_bajo_el_cursor(self, index) -> bool:
        """Si el cursor está sobre esta fila, aunque no sobre esta celda."""
        vista = self.parent()
        if vista is None:
            return False
        fila = vista.property("hoverRow")
        return fila is not None and int(fila) == index.row()

    def initStyleOption(self, option, index) -> None:  # noqa: N802 - API Qt
        super().initStyleOption(option, index)
        seleccionada = (
            option.state & QStyle.StateFlag.State_Selected
            or self._fila_seleccionada(index)
        )
        if not seleccionada:
            # El estilo nativo pinta el hover celda a celda, que es lo que
            # dejaba un solo cuadro azul bajo el cursor en vez de la fila
            # entera. Se le quita el estado y lo pinta esta clase, que sí
            # sabe de filas.
            option.state &= ~QStyle.StateFlag.State_MouseOver
            if self._fila_marcada(index):
                # Marcada es una decisión, como la selección: el color de
                # estado de la celda cede y la fila queda de un solo tono.
                option.backgroundBrush = QBrush(QColor(TABLE_CHECKED_BG))
                self._texto_claro(option)
                return
            if self._fila_bajo_el_cursor(index):
                option.backgroundBrush = QBrush(
                    _mezclado(option.backgroundBrush, TABLE_HOVER_BG)
                )
            return
        option.state &= ~QStyle.StateFlag.State_Selected
        option.backgroundBrush = QBrush(QColor(TABLE_SELECTION_BG))
        self._texto_claro(option)

    @staticmethod
    def _texto_claro(option) -> None:
        """Escribe el texto en blanco sobre las bandas de color.

        Sin el estado de selección, el estilo usaría el color normal de la
        tabla, y una fila que ya trae su propio color de letra (el verde de
        lo hecho, el gris de lo que no existe) se perdería sobre el azul.
        """
        palette = option.palette
        for role in (
            QPalette.ColorRole.Text,
            QPalette.ColorRole.WindowText,
            QPalette.ColorRole.HighlightedText,
        ):
            palette.setColor(role, QColor(TABLE_TEXT))
        option.palette = palette


class _RoundedCornerClip(QObject):
    """Recorta un widget a esquinas redondas por fuera, con una máscara.

    ``border-radius`` en la hoja de estilo solo redondea el fondo/borde que
    pinta el propio widget: una tabla dibuja las filas directamente sobre su
    viewport (no como hijos), así que ese contenido llega en escuadra hasta
    el borde y tapa el radio de las esquinas de abajo aunque las de arriba
    se vean bien (las pinta la cabecera, que sí respeta el radio). La única
    forma de que las cuatro esquinas queden iguales pase lo que pinte
    adentro es recortar el widget entero desde fuera, con una máscara que se
    recalcula cada vez que cambia de tamaño.
    """

    def __init__(self, radius: int, parent: QWidget) -> None:
        super().__init__(parent)
        self._radius = radius

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - API Qt
        if event.type() == QEvent.Type.Resize:
            self._apply(watched)
        return False

    def _apply(self, widget: QWidget) -> None:
        if widget.width() <= 0 or widget.height() <= 0:
            return
        path = QPainterPath()
        path.addRoundedRect(QRectF(widget.rect()), self._radius, self._radius)
        widget.setMask(QRegion(path.toFillPolygon().toPolygon()))


def round_corners(widget: QWidget, radius: int = TABLE_RADIUS) -> None:
    """Mantiene ``widget`` recortado a esquinas redondas mientras cambia de tamaño."""
    widget.installEventFilter(_RoundedCornerClip(radius, widget))
    if widget.width() > 0 and widget.height() > 0:
        path = QPainterPath()
        path.addRoundedRect(QRectF(widget.rect()), radius, radius)
        widget.setMask(QRegion(path.toFillPolygon().toPolygon()))


def style_data_table(table: QAbstractItemView) -> None:
    """Deja la tabla en el gris oscuro de la aplicación.

    La hoja de estilo por sí sola no basta: el estilo nativo de Windows pinta
    el viewport y las filas desde la paleta, así que se fijan también los
    roles de color. Se aplican a *todos* los grupos (``setColor`` sin grupo
    cubre Active, Inactive y Disabled) para que la tabla no vuelva al blanco
    al perder el foco ni mientras está deshabilitada durante el procesamiento.
    """
    table.setAlternatingRowColors(True)
    palette = table.palette()
    palette.setColor(QPalette.ColorRole.Base, QColor(TABLE_BASE_BG))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(TABLE_ALTERNATE_BG))
    # Sin el rol de texto, Qt seguiría escribiendo en negro sobre el gris.
    palette.setColor(QPalette.ColorRole.Text, QColor(TABLE_TEXT))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(TABLE_TEXT))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(TABLE_SELECTION_BG))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(TABLE_TEXT))
    table.setPalette(palette)
    # El viewport usa el rol ``Base``; sin autorrelleno conserva el blanco que
    # el estilo nativo pinta bajo las filas y en el área sobrante.
    viewport = table.viewport()
    viewport.setPalette(palette)
    viewport.setAutoFillBackground(True)
    table.setItemDelegate(FlatSelectionDelegate(table))
    enable_row_hover(table)
    round_corners(table)


def enable_row_hover(table: QAbstractItemView) -> None:
    """Resalta la fila entera bajo el cursor, no solo la celda."""
    table.setMouseTracking(True)
    viewport = table.viewport()
    viewport.setMouseTracking(True)
    # Solo el viewport: es quien recibe el movimiento del ratón sobre las
    # filas, y sus coordenadas son las que entiende ``indexAt``. Las del
    # widget entero llevan encima la cabecera y señalarían otra fila.
    viewport.installEventFilter(_HoverRowTracker(table))


class _HeaderScrollbarAligner(QObject):
    """Mantiene la barra vertical debajo de la cabecera de una tabla."""

    def __init__(self, table: QAbstractItemView) -> None:
        super().__init__(table)
        self._table = table

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - API Qt
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self._aplicar(watched.height())
        return False

    def _aplicar(self, alto: int) -> None:
        self._table.verticalScrollBar().setStyleSheet(
            f"QScrollBar:vertical {{ margin-top: {max(0, alto)}px; }}"
        )


# Filas que se miran al medir el ancho de una columna. Sin tope Qt las
# recorre todas, y con cuatrocientas filas eso ya es una décima de segundo
# de espera para averiguar un ancho que deciden las primeras pantallas.
RESIZE_PRECISION = 64


def size_columns_once(table, stretch_last: bool = False) -> None:
    """Ajusta las columnas al contenido una vez y las deja fijas.

    ``ResizeToContents`` no mide una vez: vuelve a medir la columna entera
    cada vez que cambia una celda. Llenar una tabla de cuatrocientas filas
    por siete columnas, u ordenarla (que reubica esas dos mil ochocientas
    celdas), pasa entonces a costar el cuadrado de las filas: ordenar la
    lista de bitácoras de un batch tardaba tres minutos y medio.

    Medir una vez y pasar a ``Interactive`` da el mismo ancho de partida y
    además deja arrastrar el borde de la columna, que con el modo por
    contenido no se podía. La última columna puede quedarse en ``Stretch``
    para que no sobre espacio a la derecha: ese modo no mide contenido, así
    que no cuesta nada.
    """
    header = table.horizontalHeader()
    ultima = table.columnCount() - 1
    for column in range(table.columnCount()):
        header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
    header.setResizeContentsPrecision(RESIZE_PRECISION)
    table.resizeColumnsToContents()
    if stretch_last and ultima >= 0:
        header.setSectionResizeMode(ultima, QHeaderView.ResizeMode.Stretch)


def align_vertical_scrollbar_to_header(table: QAbstractItemView) -> None:
    """Hace que la barra vertical empiece donde termina la cabecera."""
    cabecera = table.horizontalHeader()
    filtro = _HeaderScrollbarAligner(table)
    cabecera.installEventFilter(filtro)
    filtro._aplicar(cabecera.height())


def configure_combo_box(combo: QComboBox, minimum_contents: int = 16) -> None:
    """Aplica el comportamiento compacto de un desplegable de Windows."""
    combo.setSizeAdjustPolicy(
        QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
    )
    combo.setMinimumContentsLength(minimum_contents)
    combo.setMaxVisibleItems(12)
    view = combo.view()
    view.setTextElideMode(Qt.TextElideMode.ElideRight)
    view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    view.setUniformItemSizes(True)


def configure_menu_button(
    button: QToolButton,
    menu,
    *,
    split: bool = False,
) -> None:
    """Configura un botón de menú o un botón dividido al estilo de Windows."""
    button.setMenu(menu)
    button.setPopupMode(
        QToolButton.ToolButtonPopupMode.MenuButtonPopup
        if split
        else QToolButton.ToolButtonPopupMode.InstantPopup
    )
    button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
    button.setProperty("menuRole", "split" if split else "dropdown")

def style_dark_pane(pane: QWidget) -> None:
    """Deja un panel completo en el gris oscuro, con sus controles.

    Los hijos heredan la paleta del padre, así que fijarla aquí cubre de una
    vez las etiquetas, las flechas de los ``QToolButton`` y el texto de los
    campos: todos ellos se pintan desde roles de paleta, no desde la hoja de
    estilo, y sin esto quedarían en negro sobre el gris oscuro.
    """
    palette = pane.palette()
    palette.setColor(QPalette.ColorRole.Window, QColor(PANE_BG))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(PANE_TEXT))
    palette.setColor(QPalette.ColorRole.Base, QColor(PANE_CONTROL_BG))
    palette.setColor(QPalette.ColorRole.Text, QColor(PANE_TEXT))
    palette.setColor(QPalette.ColorRole.Button, QColor(PANE_CONTROL_BG))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(PANE_TEXT))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(TABLE_SELECTION_BG))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(PANE_TEXT))
    pane.setPalette(palette)
    pane.setAutoFillBackground(True)


def style_pdf_surface(scroll: QScrollArea) -> None:
    """Pinta de oscuro el área que rodea a la página del PDF.

    El fondo blanco que se ve detrás de la página no es del panel sino del
    *viewport* del área de desplazamiento, que Qt pinta desde el rol ``Base``
    y que ninguna regla sobre el ``QScrollArea`` alcanza. Es el mismo motivo
    por el que la tabla volvía al blanco (ver ``style_data_table``).
    """
    palette = scroll.palette()
    palette.setColor(QPalette.ColorRole.Base, QColor(PANE_SURFACE_BG))
    palette.setColor(QPalette.ColorRole.Window, QColor(PANE_SURFACE_BG))
    palette.setColor(QPalette.ColorRole.Text, QColor(PANE_TEXT))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(PANE_TEXT))
    scroll.setPalette(palette)
    viewport = scroll.viewport()
    viewport.setPalette(palette)
    viewport.setAutoFillBackground(True)


def load_zoom_icon(name: str) -> QIcon:
    """Carga un icono de zoom local para que los visores se vean igual en Windows."""
    path = _ASSETS / f"zoom_{name}.svg"
    return QIcon(str(path)) if path.is_file() else QIcon.fromTheme(f"zoom-{name}")


# Los iconos de los botones acompañan al texto: van al alto de una letra, la
# misma medida que ya usan los controles de zoom, para que la fila de botones
# no crezca ni el dibujo pese más que la palabra.
ICON_SIZE = QSize(14, 14)
# Tamaños que se guardan del dibujo: el del botón y el doble, para que se vea
# igual de limpio en una pantalla al 200 %.
_ICON_RENDER_SIZES = (ICON_SIZE.width(), ICON_SIZE.width() * 2)


def load_icon(name: str, color: QColor | str | None = None) -> QIcon:
    """Carga un icono de ``assets/`` por su nombre, sin extensión.

    Los iconos son locales por la misma razón que los del zoom: el tema de
    iconos del sistema no existe en Windows y dejar el botón sin dibujo según
    la máquina es peor que no ponerlo.

    Con ``color`` el dibujo se pinta de ese color entero. Los botones normales
    los pinta el estilo de Windows, que en tema claro los da con texto negro
    y en tema oscuro con texto blanco: un icono de color fijo se pierde en uno
    de los dos. Pintado del color del texto del botón, se lee en ambos y
    pertenece al botón en vez de estar pegado encima.
    """
    path = _ASSETS / f"{name}.svg"
    if not path.is_file():
        return QIcon()
    icon = QIcon(str(path))
    if color is None:
        return icon
    tinted = QIcon()
    for size in _ICON_RENDER_SIZES:
        pixmap = icon.pixmap(QSize(size, size))
        painter = QPainter(pixmap)
        # SourceIn conserva la transparencia del dibujo y sustituye el color:
        # el trazo queda del color pedido y los bordes suavizados se
        # mantienen, sin el recuadro que dejaría rellenar sin más.
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        painter.fillRect(pixmap.rect(), QColor(color))
        painter.end()
        tinted.addPixmap(pixmap)
    return tinted


class ZoomOverlay(QFrame):
    """Recuadro vertical de zoom que flota sobre la página.

    Lo comparten la vista previa de la ventana principal y el visor de PDF
    del visor de CSV: es el mismo control, con los mismos tamaños y el mismo
    orden (acercar, ajustar, alejar y el porcentaje debajo). Cada ventana
    aporta únicamente los textos de sus acciones, que hablan de la vista
    previa o de la página según dónde esté.
    """

    def __init__(self, zoom_in, fit, zoom_out, parent: QWidget | None = None) -> None:
        """Cada acción es ``(tooltip, nombre accesible, función)``."""
        super().__init__(parent)
        self.setObjectName("zoomOverlay")
        self.setFixedWidth(42)
        panel = QVBoxLayout(self)
        panel.setContentsMargins(5, 6, 5, 6)
        panel.setSpacing(2)

        caption = QLabel("Zoom")
        caption.setObjectName("zoomCaption")
        caption.setFixedWidth(28)
        caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        panel.addWidget(caption, 0, Qt.AlignmentFlag.AlignHCenter)

        self.btn_in = self._button("in", zoom_in, panel)
        self.btn_fit = self._button("fit", fit, panel)
        self.btn_out = self._button("out", zoom_out, panel)

        self.value_label = QLabel("100%")
        self.value_label.setObjectName("zoomValue")
        self.value_label.setFixedWidth(28)
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        panel.addWidget(self.value_label, 0, Qt.AlignmentFlag.AlignHCenter)

    def _button(self, icon: str, action, panel: QVBoxLayout) -> QToolButton:
        tooltip, accessible, slot = action
        button = QToolButton()
        button.setObjectName("zoomControl")
        button.setIcon(load_zoom_icon(icon))
        button.setIconSize(QSize(14, 14))
        button.setFixedSize(28, 28)
        button.setToolTip(tooltip)
        button.setAccessibleName(accessible)
        button.clicked.connect(slot)
        panel.addWidget(button, 0, Qt.AlignmentFlag.AlignHCenter)
        return button


class _OverlayFitWatcher(QObject):
    """Esconde un control flotante en cuanto su hueco deja de darle."""

    def __init__(self, holder: QWidget) -> None:
        super().__init__(holder)
        self._holder = holder

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - API Qt
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self.aplicar(watched.height())
        return False

    def aplicar(self, alto: int) -> None:
        cabe = alto >= self._holder.sizeHint().height()
        if self._holder.isVisible() != cabe:
            self._holder.setVisible(cabe)


def hide_overlay_when_tight(holder: QWidget) -> None:
    """El recuadro flotante se esconde si su marco no da para dibujarlo.

    Un flotante no manda sobre el mínimo del panel que lo lleva debajo (si
    lo hiciera, un control de zoom decidiría cuánto mide de mínimo la
    ventana entera), así que puede tocarle un hueco más bajo que él. Metido
    a la fuerza, sus botones de tamaño fijo se montan unos sobre otros. O
    cabe entero o no se enseña.
    """
    marco = holder.parentWidget()
    if marco is None:
        return
    vigilante = _OverlayFitWatcher(holder)
    marco.installEventFilter(vigilante)
    vigilante.aplicar(marco.height())


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


class ElidedLabel(QLabel):
    """Etiqueta informativa que se recorta con «…» en vez de ensanchar.

    Una ``QLabel`` normal pide de ancho mínimo todo su texto, así que cada
    frase larga (la estimación de tiempo, el reparto de hilos, el estado del
    procesamiento) se convertía en ancho mínimo de la ventana, y encima uno
    que crecía en marcha en cuanto se escribía un mensaje más largo que el
    anterior. Aquí el texto se pinta recortado a lo que haya de sitio y queda
    entero en el tooltip, igual que ya hacen los nombres de archivo del panel
    de avance.

    ``text()`` sigue devolviendo el texto completo, no el recortado: el
    recorte es cosa de cómo se ve la etiqueta, no de lo que dice.
    """

    # Con menos que esto el recorte no deja ni una palabra y solo se ve «…».
    MIN_ELIDED_WIDTH = 60
    # Holgura del ancho natural, para que la última letra no roce el borde.
    _TEXT_PADDING = 4

    def __init__(
        self,
        text: str = "",
        mode: Qt.TextElideMode = Qt.TextElideMode.ElideRight,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._full_text = ""
        self._elide_mode = mode
        # El tooltip automático es el texto completo, pero solo mientras nadie
        # ponga uno propio: varias de estas etiquetas llevan una explicación
        # que no se puede perder al escribirles el valor.
        self._custom_tooltip = False
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802 - API Qt
        self._full_text = text or ""
        if not self._custom_tooltip:
            QLabel.setToolTip(self, self._full_text)
        self._apply_elide()

    def text(self) -> str:
        return self._full_text

    def fullTextForCopy(self) -> str:  # noqa: N802 - API Qt
        """Texto sin el recorte visual, para copiar mensajes completos."""
        return self._full_text

    def setToolTip(self, text: str) -> None:  # noqa: N802 - API Qt
        self._custom_tooltip = bool(text)
        QLabel.setToolTip(self, text)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - API Qt
        hint = super().minimumSizeHint()
        return QSize(min(hint.width(), self.MIN_ELIDED_WIDTH), hint.height())

    def sizeHint(self) -> QSize:  # noqa: N802 - API Qt
        # Sobre el texto ya recortado el alto natural encogería y la etiqueta
        # no volvería a estirarse al ensanchar la ventana.
        hint = super().sizeHint()
        natural = self.fontMetrics().horizontalAdvance(self._full_text)
        return QSize(max(hint.width(), natural + self._TEXT_PADDING), hint.height())

    def resizeEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().resizeEvent(event)
        self._apply_elide()

    def _apply_elide(self) -> None:
        width = self.width()
        if width <= 0:
            # Antes del primer reparto no hay ancho contra el que recortar; se
            # deja entero y el ``resizeEvent`` que llega después lo ajusta.
            QLabel.setText(self, self._full_text)
            return
        QLabel.setText(
            self,
            self.fontMetrics().elidedText(self._full_text, self._elide_mode, width),
        )

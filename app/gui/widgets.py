"""Widgets Qt compartidos por las ventanas de la aplicación."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QScrollArea,
    QStyle,
    QStyledItemDelegate,
    QWidget,
)

_ASSETS = Path(__file__).resolve().parents[2] / "assets"

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
    f" border: 1px solid {TABLE_HEADER_BG};"
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


class FlatSelectionDelegate(QStyledItemDelegate):
    """Pinta la fila seleccionada como una sola banda azul.

    El estilo nativo de Windows 11 dibuja la selección como un rectángulo
    redondeado *por celda*, con margen a los lados: una fila seleccionada se
    veía como una hilera de cuadros azules sueltos en vez de una selección
    continua. Aquí se le quita a la celda el estado de selección y el azul se
    pasa como fondo del ítem, que el estilo rellena a ras del rectángulo, tal
    como ya rellena los colores de estado de la tabla. La fila queda entonces
    como una banda de un solo color, sin esquinas redondas ni huecos.
    """

    def initStyleOption(self, option, index) -> None:  # noqa: N802 - API Qt
        super().initStyleOption(option, index)
        if not (option.state & QStyle.StateFlag.State_Selected):
            return
        option.state &= ~QStyle.StateFlag.State_Selected
        option.backgroundBrush = QBrush(QColor(TABLE_SELECTION_BG))
        # Sin el estado de selección, el estilo escribiría el texto con el
        # color normal de la tabla; los roles se fijan a mano para conservar
        # el contraste sobre el azul.
        palette = option.palette
        for role in (
            QPalette.ColorRole.Text,
            QPalette.ColorRole.WindowText,
            QPalette.ColorRole.HighlightedText,
        ):
            palette.setColor(role, QColor(TABLE_TEXT))
        option.palette = palette


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

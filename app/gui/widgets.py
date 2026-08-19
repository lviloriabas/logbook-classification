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
    QAbstractItemView,
    QFrame,
    QLabel,
    QScrollArea,
    QStyle,
    QStyledItemDelegate,
    QToolButton,
    QVBoxLayout,
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

# Tipografía y controles de la aplicación. Las dos ventanas parten de esta
# hoja: sin ella el visor de CSV heredaba el estilo nativo de Windows y sus
# cuadros salían con las esquinas en pico y los botones de otro alto.
APP_CHROME_QSS = """
QMainWindow, QWidget {
    font-family: "Segoe UI", "Noto Sans", sans-serif;
    font-size: 10pt;
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
""" + ZOOM_OVERLAY_QSS


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
    round_corners(table)


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
    frase larga —la estimación de tiempo, el reparto de hilos, el estado del
    procesamiento— se convertía en ancho mínimo de la ventana, y encima uno
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

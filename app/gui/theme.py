"""Tema oscuro inspirado en Fluent y conectado con el marco de Windows."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication, QDialog, QMainWindow

from app.gui.tokens import FONT_BODY_PT, accent_color
from app.gui.widgets import (
    _APPLICATION_THEME_PROPERTY,
    APP_CHROME_QSS,
    PANE_BG,
    PANE_BORDER,
    PANE_CONTROL_BG,
    PANE_SURFACE_BG,
    PANE_TEXT,
    TABLE_ALTERNATE_BG,
)
from app.utils.app_identity import set_windows_native_window_style


class _NativeWindowTheme(QObject):
    """Aplica DWM tambien a dialogos y ventanas creados mas adelante."""

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - API Qt
        if (
            event.type() == QEvent.Type.Show
            and isinstance(watched, (QMainWindow, QDialog))
            and watched.isWindow()
        ):
            set_windows_native_window_style(watched)
        return False


def _application_font() -> QFont:
    families = set(QFontDatabase.families())
    family = (
        "Segoe UI"
        if "Segoe UI" in families
        else "Segoe UI Variable Text"
    )
    return QFont(family, FONT_BODY_PT)


def _dark_palette() -> QPalette:
    palette = QPalette()
    # El acento sale de Windows, no de un azul escrito aquí. Va también a
    # ``Highlight`` porque es lo que leen las hojas de estilo cuando piden
    # ``palette(highlight)``, que es como el acento llega a los controles sin
    # tener que reconstruir la hoja cada vez.
    acento = accent_color()
    colors = {
        QPalette.ColorRole.Window: PANE_SURFACE_BG,
        QPalette.ColorRole.WindowText: PANE_TEXT,
        QPalette.ColorRole.Base: PANE_BG,
        QPalette.ColorRole.AlternateBase: TABLE_ALTERNATE_BG,
        QPalette.ColorRole.ToolTipBase: PANE_CONTROL_BG,
        QPalette.ColorRole.ToolTipText: PANE_TEXT,
        QPalette.ColorRole.Text: PANE_TEXT,
        QPalette.ColorRole.Button: PANE_CONTROL_BG,
        QPalette.ColorRole.ButtonText: PANE_TEXT,
        QPalette.ColorRole.BrightText: PANE_TEXT,
        QPalette.ColorRole.Highlight: acento,
        QPalette.ColorRole.Accent: acento,
        QPalette.ColorRole.HighlightedText: PANE_TEXT,
        QPalette.ColorRole.Link: acento,
        QPalette.ColorRole.PlaceholderText: PANE_BORDER,
    }
    for role, color in colors.items():
        palette.setColor(role, QColor(color))
    return palette


def install_application_theme(app: QApplication) -> None:
    """Instala tipografia, paleta, controles y marcos para toda la GUI."""
    try:
        app.styleHints().setColorScheme(Qt.ColorScheme.Dark)
    except AttributeError:
        pass
    app.setFont(_application_font())
    app.setPalette(_dark_palette())
    app.setStyleSheet(APP_CHROME_QSS)
    app.setProperty(_APPLICATION_THEME_PROPERTY, True)
    native_theme = _NativeWindowTheme(app)
    app.installEventFilter(native_theme)
    app._bits_native_window_theme = native_theme

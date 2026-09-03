"""Identidad visual compartida por las ventanas PySide6."""

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from app.branding import APPLICATION_DISPLAY_NAME
from app.gui.theme import install_application_theme
from app.gui.tokens import FONT_BODY_PT, accent_color
from app.gui.widgets import (
    APP_CHROME_QSS,
    PANE_CONTROL_BG,
    PANE_SURFACE_BG,
    PANE_TEXT,
    TABLE_BASE_BG,
    window_stylesheet,
)


def test_application_theme_uses_the_fluent_dark_palette():
    app = QApplication.instance() or QApplication([])

    install_application_theme(app)

    palette = app.palette()
    assert palette.color(QPalette.ColorRole.Window) == QColor(PANE_SURFACE_BG)
    assert palette.color(QPalette.ColorRole.WindowText) == QColor(PANE_TEXT)
    assert palette.color(QPalette.ColorRole.Button) == QColor(PANE_CONTROL_BG)
    # El acento sale de Windows, no de un azul escrito en el codigo: se
    # compara con lo que el sistema diga, no con un literal.
    assert palette.color(QPalette.ColorRole.Highlight) == QColor(accent_color())
    assert palette.color(QPalette.ColorRole.Accent) == QColor(accent_color())
    assert app.styleSheet() == APP_CHROME_QSS
    assert app.font().pointSize() == FONT_BODY_PT
    assert app._bits_native_window_theme is not None
    assert window_stylesheet("QWidget { padding: 1px; }") == (
        "QWidget { padding: 1px; }"
    )


def test_application_name_describes_the_bits_workflow():
    assert APPLICATION_DISPLAY_NAME == "BITS - Clasificación de Bitácoras"


def test_group_titles_are_inside_the_frame_without_a_background_patch():
    title_rule = APP_CHROME_QSS.split("QGroupBox::title", 1)[1].split("}", 1)[0]
    assert "subcontrol-origin: border;" in title_rule
    assert "background: transparent;" in title_rule
    assert f"background-color: {TABLE_BASE_BG};" not in title_rule
    assert f"background-color: {PANE_CONTROL_BG};" not in title_rule

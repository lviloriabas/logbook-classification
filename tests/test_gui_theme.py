"""Identidad visual compartida por las ventanas PySide6."""

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from app.branding import APPLICATION_DISPLAY_NAME
from app.gui.theme import install_application_theme
from app.gui.tokens import (
    CONTROL_HOVER,
    FONT_BODY_PT,
    RADIUS_CARD,
    RADIUS_CONTROL,
    TEXT_DISABLED,
    accent_color,
)
from app.gui.widgets import (
    APP_CHROME_QSS,
    PANE_CONTROL_BG,
    PANE_SURFACE_BG,
    PANE_TEXT,
    TABLE_BASE_BG,
    accent_button_qss,
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
    # La hoja global lleva pegado el fragmento del boton de acento, que no
    # cabe en la base: sale del acento del sistema y ese no se conoce hasta
    # que hay QApplication con su paleta puesta.
    assert app.styleSheet() == APP_CHROME_QSS + accent_button_qss()
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


def test_controls_and_surfaces_share_the_six_pixel_radius():
    assert RADIUS_CONTROL == 6
    assert RADIUS_CARD == 6
    group_rule = APP_CHROME_QSS.split("QGroupBox {", 1)[1].split("}", 1)[0]
    assert "border-radius: 6px;" in group_rule


def test_el_boton_de_acento_solo_cambia_de_tono_al_pasar_y_al_pulsar():
    """Ni anillo blanco al pasar el cursor, ni marco gris al pulsar.

    Los dos venian de distinguir el estado con el marco, que es lo unico que
    se podia mover sin conocer el acento: de ``palette(highlight)`` no sale un
    tono mas claro. Con el acento delante el estado lo dice el propio color,
    que es lo que hace Windows con su boton de acento.
    """
    QApplication.instance() or QApplication([])
    acento = QColor(accent_color())
    fragmento = accent_button_qss()

    hover = _regla(fragmento, "#primaryButton:hover")
    pulsado = _regla(fragmento, "#primaryButton:pressed")
    assert PANE_TEXT not in hover
    assert CONTROL_HOVER not in hover
    assert TEXT_DISABLED not in pulsado
    # El fondo y el marco son el mismo color, asi que no hay anillo de nada.
    assert hover.count(_color(hover)) == 2
    assert pulsado.count(_color(pulsado)) == 2
    # Uno mas claro que el acento y otro mas oscuro, los dos reconocibles.
    assert QColor(_color(hover)).lightness() > acento.lightness()
    assert QColor(_color(pulsado)).lightness() < acento.lightness()


def test_la_celda_de_la_flecha_acompana_al_resto_del_boton_de_acento():
    """La flecha no se pinta con el gris de los botones divididos normales.

    Qt le pasa el ``:hover`` del widget entero a esa celda, este el cursor
    sobre el texto o sobre la flecha, asi que un color propio partia el boton
    en dos mitades distintas cada vez que el raton pasaba por encima.
    """
    QApplication.instance() or QApplication([])
    fragmento = accent_button_qss()
    celda = 'QToolButton#primaryButton[menuRole="split"]::menu-button'

    assert _color(_regla(fragmento, f"{celda}:hover")) == _color(
        _regla(fragmento, "#primaryButton:hover")
    )
    assert _color(_regla(fragmento, f"{celda}:pressed")) == _color(
        _regla(fragmento, "#primaryButton:pressed")
    )
    # La regla de la celda pulsada va despues de la del cursor: al pulsar el
    # cursor sigue encima y las dos valen, asi que decide la ultima.
    assert fragmento.index(f"{celda}:pressed") > fragmento.index(f"{celda}:hover")


def _regla(qss: str, selector: str) -> str:
    """Cuerpo de la regla de ese selector exacto."""
    marca = f"\n{selector} {{"
    assert marca in qss, selector
    return qss.split(marca, 1)[1].split("}", 1)[0]


def _color(regla: str) -> str:
    """El hex que declara la regla, para comparar reglas entre si."""
    return regla.split("background-color:", 1)[1].split(";", 1)[0].strip()

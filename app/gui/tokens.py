"""Medidas, tipografía y colores de la aplicación, en un solo sitio.

Antes cada hoja de estilo traía sus propios números y sus propios grises: la
ventana llegó a tener siete alturas distintas para controles que van en la
misma fila y veinticinco colores escritos a mano, de dos familias que no
pegaban entre sí (los de GitHub y los de Windows). Nada de eso se ve leyendo
un archivo suelto, porque el desajuste solo aparece cuando dos piezas caen
juntas en pantalla.

Aquí viven los valores y, sobre todo, las **relaciones** entre ellos: el alto
de caja sale del alto de control menos el borde, el relleno derecho del botón
dividido sale del izquierdo más la celda de la flecha. Escritas como cuentas y
no como números sueltos, no se pueden desincronizar sin que se note.

El acento no está aquí: lo pone Windows. Ver ``accent_color``.
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QGuiApplication, QPalette

# --------------------------------------------------------------------------
# Espaciado. La escala de Fluent, en múltiplos de 4. Cualquier hueco de la
# ventana tiene que salir de aquí; los números intermedios (5, 7, 9, 13…) son
# los que hacen que dos bloques parezcan mal alineados sin saber por qué.
# --------------------------------------------------------------------------
SPACE_XS = 4
SPACE_S = 8
SPACE_M = 12
SPACE_L = 16
SPACE_XL = 24

# --------------------------------------------------------------------------
# Alto de los controles. 32 px es la medida de WinUI y la que usa el panel de
# ajustes de Windows: campos, botones, desplegables y casillas comparten fila
# y tienen que compartir alto. La compacta baja a 28 para las pantallas de
# 1366x768, que es donde la ventana no cabe entera.
#
# El alto que se escribe en la hoja es el de la caja de contenido, así que hay
# que descontar el borde: Qt suma después el marco por fuera. Con el relleno
# vertical a cero la cuenta es esta y no hay que repetirla en cada regla.
# --------------------------------------------------------------------------
BORDER = 1
CONTROL_HEIGHT = 32
CONTROL_HEIGHT_COMPACT = 28
CONTROL_BOX_H = CONTROL_HEIGHT - 2 * BORDER
CONTROL_BOX_H_COMPACT = CONTROL_HEIGHT_COMPACT - 2 * BORDER

# Relleno horizontal de los controles con texto.
CONTROL_PAD_H = 12
CONTROL_PAD_H_COMPACT = 8

# Radio de esquina. Una sola medida mantiene coherentes controles, tablas y
# superficies sin introducir curvas distintas dentro de la misma ventana.
RADIUS_CONTROL = 6
RADIUS_CARD = 6

# --------------------------------------------------------------------------
# Tipografía. Cuatro papeles y ni uno más, todos en puntos.
#
# La unidad importa más que el tamaño: ``pt`` sigue el escalado de Windows y
# el cuerpo de letra que el usuario haya elegido, ``px`` no. La ventana tenía
# los dos mezclados, así que en un monitor al 150 % el panel de tiempos se
# encogía respecto a todo lo demás en lugar de crecer con ello.
# --------------------------------------------------------------------------
FONT_FAMILY = '"Segoe UI Variable Text", "Segoe UI", sans-serif'
FONT_BODY_PT = 10
FONT_CAPTION_PT = 9
FONT_SUBTITLE_PT = 14
WEIGHT_REGULAR = 400
WEIGHT_STRONG = 600

# --------------------------------------------------------------------------
# Superficies, de la más honda a la más cercana. Son los tonos oscuros de
# Windows 11: el fondo de la ventana, la tarjeta que se apoya en él y el
# control que se apoya en la tarjeta. Cada escalón sube un poco para que la
# profundidad se lea sin necesidad de sombras.
# --------------------------------------------------------------------------
WINDOW_BG = "#202020"
CARD_BG = "#2b2b2b"
CONTROL_BG = "#333333"
CONTROL_HOVER = "#3d3d3d"
CONTROL_PRESSED = "#292929"
CONTROL_DISABLED = "#282828"

STROKE = "#3d3d3d"
STROKE_STRONG = "#4a4a4a"
DIVIDER = "#2a2a2a"

TEXT = "#ffffff"
TEXT_SECONDARY = "#c5c5c5"
TEXT_TERTIARY = "#9a9a9a"
TEXT_DISABLED = "#7a7a7a"

# Estados. Los tres de Windows para texto sobre fondo oscuro; los que había
# antes venían de la paleta de GitHub y no eran los de esta plataforma.
STATUS_OK = "#6ccb5f"
STATUS_WARNING = "#fce100"
STATUS_ERROR = "#ff99a4"

# Azul de reserva: el de Windows, para cuando no hay aplicación de la que leer
# el acento del sistema todavía.
ACCENT_FALLBACK = "#0078d4"


def accent_color() -> str:
    """El color de acento que el usuario eligió en Windows.

    Se lee del sistema en lugar de fijarlo, que es lo que hace el panel de
    ajustes y lo que hace PowerToys: así la aplicación pertenece al escritorio
    en el que se abre en vez de traer su propio azul. Si todavía no hay
    ``QGuiApplication`` —al importar el módulo, por ejemplo— no hay a quién
    preguntarle, y entonces vale el de Windows por omisión.
    """
    app = QGuiApplication.instance()
    if app is None:
        return ACCENT_FALLBACK
    color = app.palette().color(QPalette.ColorRole.Accent)
    return color.name() if color.isValid() else ACCENT_FALLBACK


def blend(encima: str, debajo: str, peso: float) -> str:
    """Mezcla ``encima`` sobre ``debajo`` con el peso dado, y devuelve el hex."""
    a, b = QColor(encima), QColor(debajo)
    resto = 1.0 - peso
    return QColor(
        round(a.red() * peso + b.red() * resto),
        round(a.green() * peso + b.green() * resto),
        round(a.blue() * peso + b.blue() * resto),
    ).name()


def hover_row_color() -> str:
    """Banda de la fila que tiene el cursor encima.

    Sale del acento y no de un azul propio: si el usuario tiene el acento en
    rojo, una fila resaltada en azul no pertenece a nada. Queda muy rebajada
    sobre la tarjeta porque el cursor solo pasa por encima y no puede competir
    con la banda de la selección.
    """
    return blend(accent_color(), CARD_BG, 0.22)


def checked_row_color() -> str:
    """Banda de la fila marcada con su casilla.

    Más presente que el cursor, porque es una decisión y no un roce, pero por
    debajo de la selección, que es lo que se está mirando ahora mismo.
    """
    return blend(accent_color(), CARD_BG, 0.45)


def on_accent_text(accent: str | None = None) -> str:
    """Blanco o negro sobre el acento, el que se lea.

    El acento lo elige el usuario y puede ser un amarillo o un lima, sobre los
    que el texto blanco desaparece. Se decide por luminancia en vez de dar por
    hecho que siempre será un azul oscuro.
    """
    color = QColor(accent or accent_color())
    r, g, b = color.redF(), color.greenF(), color.blueF()

    def lineal(canal: float) -> float:
        return canal / 12.92 if canal <= 0.04045 else ((canal + 0.055) / 1.055) ** 2.4

    luminancia = 0.2126 * lineal(r) + 0.7152 * lineal(g) + 0.0722 * lineal(b)
    return "#000000" if luminancia > 0.45 else "#ffffff"

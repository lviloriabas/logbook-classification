"""Adaptación de las ventanas a la pantalla en la que se abren.

La carpeta se copia a equipos con pantallas muy distintas (portátiles de
1366x768, monitores 1080p con el escalado de Windows al 125% o 150%, pantallas
4K) y la interfaz tiene que caber en todas: ni abrirse más grande que el área
de trabajo ni recortar controles por abajo.

Aquí viven las dos piezas que lo resuelven:

* ``fitted_geometry`` calcula con qué tamaño y en qué posición entra una
  ventana completa en el área disponible (la de trabajo, ya sin la barra de
  tareas), descontando el marco que dibuja el sistema alrededor.
* ``Density`` es el juego de medidas con el que se dibuja la ventana.
  ``ROOMY`` son las de siempre, las mismas que ya trae ``APP_CHROME_QSS``, y
  ``COMPACT`` las aprieta cuando la ventana no llega a ``COMPACT_HEIGHT`` de
  alto. Solo cambian espacios y altos: los colores, las tipografías y el radio
  de 6 px de los cuadros son los mismos en las dos, porque la ventana
  compacta tiene que ser la misma aplicación, no otra.

Por eso ``ROOMY.qss`` está vacía: las medidas holgadas ya las fija la hoja
base y repetirlas aquí sería tener el mismo número en dos sitios. Solo la
compacta añade su fragmento, que se pega al final de la hoja para ganarle a
las reglas de igual especificidad.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QRect, QSize
from PySide6.QtGui import QGuiApplication

# Hueco que Windows se queda alrededor del área de cliente: los bordes a los
# lados y la barra de título arriba. Se descuenta al calcular el tamaño para
# que la ventana entre con su marco puesto y no quede la barra de título por
# encima del borde de la pantalla.
FRAME_ALLOWANCE = QSize(16, 48)

# Ninguna ventana baja de aquí aunque la pantalla sea diminuta: por debajo no
# queda sitio para el contenido y es mejor que asome antes de encogerse más.
MIN_WINDOW = QSize(560, 380)

# Alto de ventana por debajo del cual se pasa a medidas compactas. Un cliente
# de 1366x768 deja 689 px útiles y uno de 1080p al 150% deja 641: los dos caen
# de este lado. Un 1080p sin escalar deja 1001 y se queda con las holgadas.
COMPACT_HEIGHT = 820

# Al arrastrar el borde el alto pasa por el umbral muchas veces seguidas. Se
# vuelve a las medidas holgadas un poco más arriba de donde se dejaron, para
# que la ventana no parpadee entre las dos mientras se redimensiona.
DENSITY_HYSTERESIS = 40

_COMPACT_QSS = """
QPushButton { min-height: 20px; padding: 2px 8px; }
/* El botón con dibujo cede el aire de los lados, que el icono ya llena: la
   fila de «Entrada» es la más ancha de la ventana y, si crece, los cuadros de
   arriba dejan de caber en dos columnas justo en el escritorio de 1280 px,
   que es donde ese reparto hace falta. */
QPushButton#iconButton { padding-left: 4px; padding-right: 6px; }
QToolButton { padding: 1px 4px; }
QToolButton#primaryButton { min-height: 20px; padding: 2px 8px; }
QGroupBox { margin-top: 6px; padding: 4px 6px 3px 6px; }
QSpinBox, QComboBox, QLineEdit { padding: 1px; }
"""


@dataclass(frozen=True)
class Density:
    """Medidas de la ventana para un tamaño de pantalla dado.

    Las que no caben en una hoja de estilo (márgenes de layout, altos y anchos
    mínimos, el ancho de la columna de nombres del panel de avance) viajan como
    números, porque el código de construcción las pide una a una.
    """

    name: str
    # Ventana principal: márgenes del layout raíz y separación entre bloques.
    window_margin: int
    root_spacing: int
    group_spacing: int
    # Interior de los cuadros «Entrada», «Procesamiento» y «Salidas».
    group_margin_v: int
    group_row_spacing: int
    group_column_spacing: int
    # Franja de abajo: consola y panel de avance por archivo.
    bottom_pane_height: int
    bottom_min_height: int
    log_min_width: int
    name_column_width: int
    # Vista previa: mínimo por debajo del cual la página ya no se lee. Es un
    # escaneo vertical de una hoja completa, así que el suelo va por encima
    # del de un recuadro cualquiera; el reparto habitual lo decide el
    # separador de la ventana, no este número.
    preview_min_width: int
    preview_min_height: int
    # Visor de CSV y editor de plantillas.
    pdf_pane_min_width: int
    pdf_pane_min_height: int
    editor_view_min_width: int
    editor_panel_max_width: int
    # Ventana de AirVault: las tres piezas elásticas que se apilan en ella
    # (las dos tablas, la bitácora y el resumen de abajo). Lo que suman es
    # lo que decide si la ventana entra en un escritorio bajo o si el
    # reparto tiene que apretar los controles unos contra otros.
    airvault_table_min_height: int
    airvault_log_min_height: int
    airvault_summary_min_height: int
    qss: str = ""

    @property
    def compact(self) -> bool:
        return self is COMPACT


ROOMY = Density(
    name="holgada",
    window_margin=8,
    root_spacing=5,
    group_spacing=4,
    group_margin_v=5,
    group_row_spacing=6,
    group_column_spacing=8,
    bottom_pane_height=190,
    bottom_min_height=150,
    log_min_width=340,
    name_column_width=220,
    preview_min_width=340,
    preview_min_height=260,
    pdf_pane_min_width=360,
    pdf_pane_min_height=260,
    editor_view_min_width=600,
    editor_panel_max_width=340,
    airvault_table_min_height=132,
    airvault_log_min_height=160,
    airvault_summary_min_height=48,
)

COMPACT = Density(
    name="compacta",
    window_margin=5,
    root_spacing=3,
    group_spacing=3,
    group_margin_v=3,
    group_row_spacing=3,
    group_column_spacing=4,
    bottom_pane_height=140,
    # Lo que de verdad pide el panel de avance apretado: sus tres rótulos
    # enteros más una fila de la lista, que es la parte que se desplaza.
    # Con los 72 px de antes (los que se le quitaron para hacerle sitio a la
    # línea de pasos) el reparto no llegaba a los rótulos y los dejaba en
    # once píxeles: «Avance por archivo» salía partido por la mitad y
    # «Sin archivos procesados aún.» cortado por abajo.
    bottom_min_height=100,
    log_min_width=200,
    name_column_width=150,
    preview_min_width=200,
    # Los píxeles que gana el panel de avance salen de aquí, que es lo que
    # se puede ceder sin cortar nada: la página se desplaza dentro de su
    # marco. Es un suelo que solo se toca con la ventana en su tamaño más
    # pequeño; en cualquier otro, el separador le da mucho más.
    preview_min_height=112,
    pdf_pane_min_width=260,
    pdf_pane_min_height=160,
    editor_view_min_width=360,
    editor_panel_max_width=300,
    # La ventana de AirVault pide 789 px de alto con las medidas holgadas y
    # un escritorio de 1366x768 solo da 728: al recortarla, el reparto
    # empujaba el resumen de abajo por encima de la bitácora y la frase se
    # leía sobre la lista. Apretadas, las tres piezas suman 118 px menos y
    # la ventana entra entera hasta en un escritorio de 1280x720.
    airvault_table_min_height=100,
    airvault_log_min_height=120,
    airvault_summary_min_height=34,
    qss=_COMPACT_QSS,
)


def density_for(
    height: int,
    current: Density | None = None,
    threshold: int = COMPACT_HEIGHT,
) -> Density:
    """Medidas que le tocan a una ventana de ``height`` px de alto.

    ``current`` es la densidad que ya está aplicada: sirve para la histéresis,
    de modo que volver a lo holgado exija recuperar algo más de alto del que
    se perdió al apretar.

    ``threshold`` es el alto por debajo del cual hay que apretarse. Por
    defecto es ``COMPACT_HEIGHT``, la estimación que sirve para elegir con
    qué medidas se construye una ventana; la que ya está construida pasa el
    alto que de verdad pide su reparto holgado, que depende de la tipografía
    del equipo. Con el número estimado, una ventana entre ese valor y lo que
    el reparto holgado necesita se quedaba con medidas que no caben, y el
    layout, sin sitio, encogía los cuadros por debajo de su mínimo: es lo que
    monta unos controles encima de otros.
    """
    if current is COMPACT:
        return ROOMY if height >= threshold + DENSITY_HYSTERESIS else COMPACT
    return COMPACT if height < threshold else ROOMY


def fitted_geometry(
    preferred: QSize,
    available: QRect,
    frame: QSize = FRAME_ALLOWANCE,
) -> QRect:
    """Geometría con la que ``preferred`` entra centrada en ``available``.

    Devuelve el rectángulo del área de cliente: el ancho y el alto son los de
    ``resize`` y la esquina es la de ``move``, que en una ventana de primer
    nivel posiciona el marco. Por eso el marco se descuenta del sitio
    disponible tanto al recortar el tamaño como al centrar.
    """
    # El suelo es del techo, no del tamaño: si el área disponible es
    # absurdamente pequeña se prefiere que la ventana asome antes que dejarla
    # sin contenido, pero un diálogo que pide poco se queda con lo que pide.
    max_width = max(MIN_WINDOW.width(), available.width() - frame.width())
    max_height = max(MIN_WINDOW.height(), available.height() - frame.height())
    width = min(preferred.width(), max_width)
    height = min(preferred.height(), max_height)
    x = available.x() + max(0, (available.width() - width - frame.width()) // 2)
    y = available.y() + max(0, (available.height() - height - frame.height()) // 2)
    return QRect(x, y, width, height)


def available_area(widget=None) -> QRect:
    """Área de trabajo de la pantalla donde está ``widget``.

    Sin ``widget`` (o antes de que la ventana tenga pantalla asignada) se usa
    la principal, que es donde el sistema va a abrirla.
    """
    screen = None
    if widget is not None:
        screen = widget.screen()
    if screen is None:
        screen = QGuiApplication.primaryScreen()
    if screen is None:  # sin pantallas (entornos sin escritorio)
        return QRect(0, 0, 1024, 768)
    return screen.availableGeometry()


def fit_to_screen(window, width: int, height: int) -> Density:
    """Abre ``window`` con el tamaño pedido, recortado a lo que da la pantalla.

    Devuelve la densidad que le corresponde al alto resultante, que es la que
    la ventana tiene que aplicarse antes de construirse.
    """
    geometry = fitted_geometry(QSize(width, height), available_area(window))
    window.resize(geometry.size())
    window.move(geometry.topLeft())
    return density_for(geometry.height())

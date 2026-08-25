"""La interfaz cabe en la pantalla del equipo, sea cual sea.

La carpeta se copia a portátiles de 1366x768 y a monitores 1080p con el
escalado de Windows al 125% o al 150%, que dejan un escritorio lógico de
1536x864 o de 1280x720. La ventana principal pedía 1280x800 y su mínimo real
era 1447x886: en todas esas pantallas menos en un 1080p sin escalar se abría
más grande que el escritorio y la franja de abajo (la consola y el panel de
avance) quedaba fuera, sin forma de alcanzarla.

Estas pruebas fijan las dos condiciones que lo arreglan: que ninguna ventana
se abra más grande que el área de trabajo, y que con ese tamaño no le sobre
contenido, es decir que su tamaño mínimo quepa dentro. Se comprueban con la
tipografía real del sistema, porque con la que pone la plataforma «offscreen»
las medidas de texto no se parecen a las de Windows y la prueba no diría nada.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect, QSize
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

import app.gui.responsive as responsive
from app.gui.csv_viewer import CsvViewerWindow
from app.gui.editor_window import EditorWindow
from app.gui.field_selector import ImportantFieldsDialog
from app.gui.fleet_editor import FleetEditorDialog, FleetStore
from app.gui.main_window import _PREFERRED_HEIGHT, _PREFERRED_WIDTH, MainWindow
from app.gui.responsive import (
    COMPACT,
    FRAME_ALLOWANCE,
    ROOMY,
    density_for,
    fitted_geometry,
)
from app.gui.widgets import ElidedLabel

# Pantallas reales, con el escalado ya aplicado: lo que Qt ve como escritorio
# es el tamaño físico dividido por el factor de Windows.
PANTALLAS = [
    ("1024x768", 1024, 768),
    ("1280x720 (1080p al 150%)", 1280, 720),
    ("1366x768", 1366, 768),
    ("1440x900", 1440, 900),
    ("1536x864 (1080p al 125%)", 1536, 864),
    ("1600x900", 1600, 900),
    ("1920x1080", 1920, 1080),
    ("2560x1440", 2560, 1440),
    ("3840x2160", 3840, 2160),
]

# Alto que se lleva la barra de tareas de Windows en su tamaño de siempre.
BARRA_DE_TAREAS = 40


def _app() -> QApplication:
    """Aplicación con la tipografía de Windows, no con la de «offscreen».

    Los anchos mínimos de la ventana salen de medir texto. La plataforma sin
    pantalla no resuelve ninguna familia real y devuelve medidas que no son
    las del equipo del usuario, así que se carga Segoe UI (la que declara la
    hoja de estilo) desde la carpeta de fuentes del sistema.
    """
    app = QApplication.instance() or QApplication([])
    if getattr(app, "_fuente_real", False):
        return app
    fuentes = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    familias: list[str] = []
    for archivo in ("segoeui.ttf", "segoeuib.ttf", "seguisb.ttf"):
        identificador = QFontDatabase.addApplicationFont(str(fuentes / archivo))
        familias += QFontDatabase.applicationFontFamilies(identificador)
    if "Segoe UI" not in familias:
        pytest.skip("sin la tipografía del sistema las medidas no son las reales")
    app.setFont(QFont("Segoe UI", 10))
    app._fuente_real = True
    return app


def _area_de_trabajo(ancho: int, alto: int) -> QRect:
    """Escritorio útil de una pantalla, ya sin la barra de tareas."""
    return QRect(0, 0, ancho, alto - BARRA_DE_TAREAS)


def _cabe(ventana, area: QRect) -> bool:
    """Si la ventana entera, con su marco, está dentro del área de trabajo."""
    tamano = ventana.size()
    return (
        tamano.width() + FRAME_ALLOWANCE.width() <= area.width()
        and tamano.height() + FRAME_ALLOWANCE.height() <= area.height()
    )


def _sin_recortes(ventana) -> bool:
    """Si el contenido cabe en la ventana: nada empujado fuera del borde."""
    minimo = ventana.minimumSizeHint()
    return (
        minimo.width() <= ventana.width()
        and minimo.height() <= ventana.height()
    )


# ── El cálculo de tamaño, sin ventanas de por medio ──────────────────────

@pytest.mark.parametrize("nombre,ancho,alto", PANTALLAS)
def test_la_ventana_calculada_entra_en_el_escritorio(nombre, ancho, alto):
    area = _area_de_trabajo(ancho, alto)
    geometria = fitted_geometry(QSize(_PREFERRED_WIDTH, _PREFERRED_HEIGHT), area)
    assert geometria.width() + FRAME_ALLOWANCE.width() <= area.width(), nombre
    assert geometria.height() + FRAME_ALLOWANCE.height() <= area.height(), nombre
    assert area.contains(geometria), nombre


def test_no_agranda_a_quien_pide_poco():
    """Un diálogo pequeño se queda con su tamaño; el recorte es solo techo."""
    geometria = fitted_geometry(QSize(420, 520), _area_de_trabajo(1920, 1080))
    assert geometria.size() == QSize(420, 520)


def test_la_ventana_se_centra_en_el_area_disponible():
    area = QRect(0, 40, 1920, 1000)
    geometria = fitted_geometry(QSize(1280, 900), area)
    izquierda = geometria.x() - area.x()
    derecha = area.right() - geometria.right()
    assert abs(izquierda - derecha) <= FRAME_ALLOWANCE.width()
    assert geometria.y() >= area.y()


def test_un_escritorio_diminuto_no_deja_la_ventana_sin_contenido():
    """Con menos sitio del razonable se prefiere que asome a que se anule."""
    geometria = fitted_geometry(QSize(1280, 900), QRect(0, 0, 320, 240))
    assert geometria.width() >= responsive.MIN_WINDOW.width()
    assert geometria.height() >= responsive.MIN_WINDOW.height()


# ── Elección de medidas ──────────────────────────────────────────────────

def test_las_pantallas_bajas_usan_medidas_compactas():
    assert density_for(680) is COMPACT
    assert density_for(1001) is ROOMY


def test_la_densidad_no_parpadea_al_arrastrar_el_borde():
    """Volver a lo holgado exige recuperar más alto del que se perdió."""
    justo_encima = responsive.COMPACT_HEIGHT + 1
    assert density_for(justo_encima, COMPACT) is COMPACT
    assert density_for(justo_encima + responsive.DENSITY_HYSTERESIS, COMPACT) is ROOMY


def test_las_medidas_holgadas_no_tocan_nada():
    """Son las de la hoja base: la ventana grande se dibuja como siempre."""
    assert ROOMY.qss == ""
    assert COMPACT.qss != ""


# ── Las ventanas, abiertas en cada pantalla ──────────────────────────────

@pytest.mark.parametrize("nombre,ancho,alto", PANTALLAS)
def test_la_ventana_principal_cabe_entera(nombre, ancho, alto):
    _app()
    area = _area_de_trabajo(ancho, alto)
    with patch.object(responsive, "available_area", return_value=area):
        ventana = MainWindow()
        try:
            ventana.show()
            assert _cabe(ventana, area), f"{nombre}: se abre fuera del escritorio"
            assert _sin_recortes(ventana), f"{nombre}: le sobra contenido"
        finally:
            ventana.close()


@pytest.mark.parametrize("nombre,ancho,alto", PANTALLAS)
def test_las_demas_ventanas_caben_enteras(nombre, ancho, alto):
    _app()
    area = _area_de_trabajo(ancho, alto)
    carpeta = Path(tempfile.mkdtemp())
    with patch.object(responsive, "available_area", return_value=area):
        ventanas = [
            CsvViewerWindow(carpeta),
            EditorWindow(),
            ImportantFieldsDialog(["log_number", "fecha"], ["fecha"]),
            FleetEditorDialog(FleetStore(carpeta / "fleet.json")),
        ]
        try:
            for ventana in ventanas:
                ventana.show()
                etiqueta = f"{nombre}: {type(ventana).__name__}"
                assert _cabe(ventana, area), f"{etiqueta} se abre fuera"
                assert _sin_recortes(ventana), f"{etiqueta} le sobra contenido"
        finally:
            for ventana in ventanas:
                ventana.close()


def test_en_pantalla_baja_los_cuadros_se_reparten_en_dos_columnas():
    """El alto que se ahorra es el que necesita la vista previa."""
    _app()
    area = _area_de_trabajo(1366, 768)
    with patch.object(responsive, "available_area", return_value=area):
        ventana = MainWindow()
        try:
            ventana.show()
            assert ventana._density is COMPACT
            assert ventana._controls_columns == 2
            apilados = sum(
                cuadro.minimumSizeHint().height()
                for cuadro in (
                    ventana._input_group,
                    ventana._process_group,
                    ventana._options_group,
                )
            )
            reparto = ventana._controls_grid.parentWidget().minimumSizeHint()
            assert reparto.height() < apilados
        finally:
            ventana.close()


def test_en_pantalla_grande_la_ventana_es_la_de_siempre():
    """Nada de esto puede cambiar el aspecto donde ya cabía: una columna."""
    _app()
    area = _area_de_trabajo(1920, 1080)
    with patch.object(responsive, "available_area", return_value=area):
        ventana = MainWindow()
        try:
            ventana.show()
            assert ventana._density is ROOMY
            assert ventana._controls_columns == 1
        finally:
            ventana.close()


def test_la_ventana_se_readapta_al_cambiar_de_tamano():
    """Maximizar, restaurar o mover a otro monitor recalcula las medidas."""
    _app()
    grande = _area_de_trabajo(1920, 1080)
    with patch.object(responsive, "available_area", return_value=grande):
        ventana = MainWindow()
        try:
            ventana.show()
            assert ventana._density is ROOMY
            ventana.resize(1366, 680)
            assert ventana._density is COMPACT
            assert ventana._controls_columns == 2
            ventana.resize(1400, 950)
            assert ventana._density is ROOMY
            assert ventana._controls_columns == 1
        finally:
            ventana.close()


# ── Etiquetas que no pueden mandar sobre el ancho de la ventana ──────────

def test_un_mensaje_largo_no_ensancha_la_ventana():
    """El estado del procesamiento crecía con cada mensaje más largo."""
    _app()
    area = _area_de_trabajo(1366, 768)
    with patch.object(responsive, "available_area", return_value=area):
        ventana = MainWindow()
        try:
            ventana.show()
            antes = ventana.minimumSizeHint().width()
            ventana.status_label.setText(
                "Archivo 12/40: bitacora-YV1234-enero-2026-revisada.pdf - "
                "reconociendo páginas 1234/5678 con la plantilla activa"
            )
            assert ventana.minimumSizeHint().width() <= antes
        finally:
            ventana.close()


def test_la_etiqueta_recortada_conserva_su_texto():
    """Se recorta lo que se pinta, no lo que la etiqueta dice."""
    _app()
    etiqueta = ElidedLabel("Distribución automática: 4 procesos x 2 hilos")
    etiqueta.show()
    etiqueta.resize(80, 20)
    assert etiqueta.text() == "Distribución automática: 4 procesos x 2 hilos"
    assert etiqueta.toolTip() == etiqueta.text()
    assert etiqueta.minimumSizeHint().width() <= ElidedLabel.MIN_ELIDED_WIDTH
    etiqueta.close()


def test_una_explicacion_propia_gana_al_tooltip_automatico():
    _app()
    etiqueta = ElidedLabel("")
    etiqueta.setToolTip("Rango de páginas contando el batch entero")
    etiqueta.setText("de 35 pág.")
    assert etiqueta.toolTip() == "Rango de páginas contando el batch entero"

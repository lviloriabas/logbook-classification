"""Lo que se escribe en la fila de consulta tiene que caber y verse entero.

Las dos medidas que fija esta ventana no salen de un capricho de diseño:

* Qt pide el ancho del campo de fecha midiendo sus dos límites, y los
  dos (1/1/2000 y el día de hoy) llevan día y mes de una cifra. Con ese
  ancho una fecha como 30/12/2025 no entraba y el año se cortaba.
* El desplegable de «Mostrar» pedía sitio para 26 mayúsculas, casi el doble de
  lo que miden sus tres opciones, y encima se llevaba el estiramiento de la
  fila: se estiraba hasta el borde de la ventana y ese exceso salía de los
  campos de fecha, que se quedaban en su mínimo.
"""

from __future__ import annotations

import pytest

from PySide6.QtCore import QDate, QRect

from app.gui import responsive
from app.gui.web_reports_window import FECHA_MAS_LARGA, WebReportsWindow


@pytest.fixture
def pantalla(monkeypatch):
    """Finge el escritorio disponible, que offscreen no sabe medir."""

    def fijar(ancho: int, alto: int) -> None:
        monkeypatch.setattr(
            responsive,
            "available_area",
            lambda _w=None: QRect(0, 0, ancho, alto),
        )

    return fijar


@pytest.mark.parametrize("alto", [1080, 768])
def test_la_fecha_mas_larga_se_ve_entera(app, pantalla, tmp_path, alto):
    """Con las dos densidades: la baja aprieta el relleno de los lados."""
    pantalla(1366, alto)
    ventana = WebReportsWindow(tmp_path)
    try:
        ventana.show()
        app.processEvents()

        for campo in (ventana.desde_edit, ventana.hasta_edit):
            campo.setDate(QDate(2025, 12, 30))
        app.processEvents()

        for campo in (ventana.desde_edit, ventana.hasta_edit):
            escrito = campo.fontMetrics().horizontalAdvance(campo.text())
            assert campo.lineEdit().width() >= escrito, (
                f"{campo.text()} no cabe en el campo y se corta por el final"
            )
    finally:
        ventana.close()
        app.processEvents()


def test_la_fecha_mas_larga_es_la_de_dos_cifras(app, tmp_path):
    """El patrón con el que se mide no puede quedarse corto ante otra fecha."""
    ventana = WebReportsWindow(tmp_path)
    try:
        fuente = ventana.desde_edit.fontMetrics()
        formato = ventana.desde_edit.displayFormat()
        patron = fuente.horizontalAdvance(
            FECHA_MAS_LARGA.toString(formato)
        )
        for mes in (1, 9, 10, 12):
            dias = QDate(2025, mes, 1).daysInMonth()
            for dia in (1, 9, 10, 28, dias):
                fecha = QDate(2025, mes, dia)
                ancho = fuente.horizontalAdvance(fecha.toString(formato))
                assert ancho <= patron, (
                    f"{fecha.toString(formato)} mide más que el patrón"
                )
    finally:
        ventana.close()


def test_mostrar_no_se_estira_hasta_el_borde(app, pantalla, tmp_path):
    pantalla(1920, 1080)
    ventana = WebReportsWindow(tmp_path)
    try:
        ventana.show()
        ventana.resize(1280, 800)
        app.processEvents()

        combo = ventana.filtro_combo
        fuente = combo.fontMetrics()
        opcion = max(
            fuente.horizontalAdvance(combo.itemText(indice))
            for indice in range(combo.count())
        )
        # El hueco de la flecha y el relleno de los lados suman menos de cien
        # píxeles; el resto sería sitio pedido y no usado.
        assert combo.width() < opcion + 100, (
            "el desplegable pide mucho más de lo que miden sus opciones"
        )
        assert combo.width() >= opcion, (
            "las opciones no caben y se leerían recortadas"
        )
        # Y lo que sobra en la fila se queda a su derecha, no dentro.
        borde = ventana.width() - ventana._densidad.window_margin
        assert combo.geometry().right() < borde - 100
    finally:
        ventana.close()
        app.processEvents()

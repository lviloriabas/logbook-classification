"""La vista previa y la lista de bitácoras son ventanas aparte y responden.

Eran cuadros modales colgados de la ventana de AirVault: bloqueaban la
ventana desde la que se abrían (que puede estar subiendo una entrega) y
Windows no les daba entrada propia en la barra de tareas. Y la lista de
bitácoras de un batch de cuatrocientas páginas tardaba tres minutos y medio
en ordenarse por una columna, porque medir el ancho por contenido obliga a
Qt a repasar la columna entera cada vez que cambia una celda.
"""

from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QHeaderView

from app.airvault.flujo import BatchPrevisto
from app.airvault.model import Registro
from app.gui.airvault_previa import BitacorasDelBatch, VistaPreviaBatches


#: Columna «Log Page» de la lista de páginas de un batch.
LOG_PAGE = 3


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _bitacora(seq: int) -> Registro:
    return Registro(
        seq=seq,
        archivo_origen="paginas.pdf",
        pagina_origen=seq,
        matricula=f"HP-{1800 + seq % 40}CMP",
        log_number=str(2271000 + (seq * 7) % 900),
        flight_number="703",
        fecha="2026/08/11",
    )


def _batch(cuantas: int = 400) -> list[Registro]:
    return [_bitacora(seq) for seq in range(1, cuantas + 1)]


def _previsto(nombre="DP | BITS", registros=None) -> BatchPrevisto:
    return BatchPrevisto(
        nombre=nombre,
        parte=1,
        partes=1,
        revisar=False,
        pdf="entrega.pdf",
        registros=list(registros or [_bitacora(1)]),
    )


# ── ventanas aparte ────────────────────────────────────────────────

def test_la_lista_de_bitacoras_es_una_ventana_propia(app):
    cuadro = BitacorasDelBatch("DP | BITS", _batch(3))
    try:
        # Sin dueño en Qt: con dueño, Windows no le da su botón en la barra
        # de tareas y la deja pegada a la ventana de arriba.
        assert cuadro.parent() is None
        assert cuadro.windowType() == Qt.WindowType.Window
        assert cuadro.windowFlags() & Qt.WindowType.WindowMinimizeButtonHint
        # Y con su propia hoja de estilo, que sin dueño no se hereda.
        assert "QPushButton" in cuadro.styleSheet()
    finally:
        cuadro.visor.shutdown()
        cuadro.close()
        app.processEvents()


def test_la_vista_previa_tambien_es_una_ventana_propia(app):
    cuadro = VistaPreviaBatches([_previsto()])
    try:
        assert cuadro.parent() is None
        assert cuadro.windowType() == Qt.WindowType.Window
        assert "QPushButton" in cuadro.styleSheet()
    finally:
        cuadro.close()
        app.processEvents()


def test_abrir_las_bitacoras_no_bloquea_la_lista_de_batches(app):
    """Se pueden mirar dos batches a la vez, y la lista sigue viva."""
    cuadro = VistaPreviaBatches([_previsto(registros=_batch(3))])
    try:
        cuadro.tabla.selectRow(0)
        cuadro._abrir_bitacoras()

        assert len(cuadro._abiertas) == 1
        ventana = cuadro._abiertas[0]
        assert not ventana.isModal()
        assert ventana.isVisible()
        # La referencia la conserva quien la abrió: sin ella, Qt la
        # destruiría al volver de este método.
        assert ventana.parent() is None
    finally:
        for ventana in list(cuadro._abiertas):
            ventana.visor.shutdown()
        cuadro.close()
        app.processEvents()


def test_cerrar_la_lista_de_batches_se_lleva_sus_ventanas(app):
    """Dejarlas sueltas mantendría el programa abierto sin nada que mirar."""
    cuadro = VistaPreviaBatches([_previsto(registros=_batch(3))])
    cuadro.tabla.selectRow(0)
    cuadro._abrir_bitacoras()
    cuadro._abiertas[0].visor.shutdown()

    cuadro.close()
    app.processEvents()

    # Cerrarla la destruye, y al destruirse suelta la referencia: son
    # ventanas de consulta y no tiene sentido acumularlas.
    assert cuadro._abiertas == []


# ── responde con un batch entero ───────────────────────────────────

def test_las_columnas_se_miden_una_vez_y_quedan_ajustables(app):
    """Medir por contenido en cada cambio es lo que la volvía lenta."""
    cuadro = BitacorasDelBatch("DP | BITS", _batch(20))
    try:
        cabecera = cuadro.tabla.horizontalHeader()
        ultima = cuadro.tabla.columnCount() - 1
        for columna in range(ultima):
            assert cabecera.sectionResizeMode(columna) == (
                QHeaderView.ResizeMode.Interactive
            ), f"la columna {columna} vuelve a medirse en cada celda"
        # La última se estira para que no sobre espacio a la derecha; ese
        # modo no mide contenido, así que no cuesta nada.
        assert cabecera.sectionResizeMode(ultima) == (
            QHeaderView.ResizeMode.Stretch
        )
        # Y el ancho de partida sigue saliendo del contenido.
        assert cuadro.tabla.columnWidth(0) > 0
    finally:
        cuadro.visor.shutdown()
        cuadro.close()
        app.processEvents()


def test_ordenar_un_batch_entero_es_inmediato(app):
    """Cuatrocientas bitácoras por ocho columnas: eran 218 segundos."""
    cuadro = BitacorasDelBatch("DP | BITS", _batch(400))
    try:
        assert cuadro.tabla.rowCount() == 400

        inicio = time.monotonic()
        cuadro.orden.cycle_column(LOG_PAGE)  # de mayor a menor
        app.processEvents()
        tardanza = time.monotonic() - inicio

        assert (
            cuadro.tabla.item(0, LOG_PAGE).text()
            >= cuadro.tabla.item(1, LOG_PAGE).text()
        )
        # Margen enorme a propósito: lo que se vigila es el orden de
        # magnitud, no el rendimiento del equipo que corra la prueba.
        assert tardanza < 5.0, f"ordenar tardó {tardanza:.1f} s"
    finally:
        cuadro.visor.shutdown()
        cuadro.close()
        app.processEvents()


def test_elegir_una_fila_de_un_batch_entero_es_inmediato(app):
    cuadro = BitacorasDelBatch("DP | BITS", _batch(400))
    try:
        inicio = time.monotonic()
        cuadro.tabla.selectRow(200)
        app.processEvents()

        assert time.monotonic() - inicio < 5.0
    finally:
        cuadro.visor.shutdown()
        cuadro.close()
        app.processEvents()

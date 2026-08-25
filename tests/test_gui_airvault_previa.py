"""Los cuadros de la vista previa: los batches y las bitácoras de cada uno.

No tocan la red ni el disco: reciben lo que el módulo ya calculó y solo
tienen que enseñarlo entero. Lo que se comprueba es que no se pierda ni se
invente ninguna fila, que los separadores no pasen por bitácoras y que
elegir un batch lleve a su lista.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.airvault.flujo import BatchPrevisto
from app.airvault.model import EstadoRegistro, Registro
from app.gui.airvault_previa import BitacorasDelBatch, VistaPreviaBatches


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def bitacora(seq, matricula="HP-1848CMP", log="2271620", **extra):
    return Registro(
        seq=seq,
        archivo_origen="paginas.pdf",
        pagina_origen=seq,
        matricula=matricula,
        log_number=log,
        flight_number="703",
        fecha="2026/08/11",
        **extra,
    )


def separador(seq, etiqueta="HP-1848CMP"):
    return Registro(seq=seq, separador=etiqueta)


def previsto(nombre="DP | BITS", registros=None, **extra):
    return BatchPrevisto(
        nombre=nombre,
        parte=1,
        partes=1,
        revisar=False,
        pdf="entrega.pdf",
        registros=list(registros or []),
        **extra,
    )


# ── la lista de bitácoras de un batch ──────────────────────────────

def test_los_separadores_no_cuentan_como_bitacoras(app):
    """Ocupan página en el batch, pero no son documentos que indexar."""
    registros = [separador(1), bitacora(2), bitacora(3, log="2271621")]

    cuadro = BitacorasDelBatch("DP | BITS", registros)

    assert cuadro.tabla.rowCount() == 2
    assert cuadro.tabla.item(0, 2).text() == "2271620"
    assert cuadro.tabla.item(1, 2).text() == "2271621"


def test_cada_bitacora_dice_la_pagina_que_ocupa_en_el_batch(app):
    """Es el número con el que se la busca en Web Index."""
    registros = [separador(1), bitacora(2), bitacora(3, log="2271621")]

    cuadro = BitacorasDelBatch("DP | BITS", registros)

    assert cuadro.tabla.item(0, 0).text() == "2"
    assert cuadro.tabla.item(1, 0).text() == "3"
    assert cuadro.tabla.item(0, 5).text() == "paginas.pdf, p. 2"


def test_un_aviso_manda_sobre_el_estado(app):
    """Lo que importa de una página bloqueada es por qué lo está."""
    registros = [
        bitacora(1),
        bitacora(2, log="2271621", avisos=["sin matricula"]),
        bitacora(3, log="2271622", estado=EstadoRegistro.ESCRITA),
    ]

    cuadro = BitacorasDelBatch("DP | BITS", registros)

    assert cuadro.tabla.item(0, 6).text() == "Por escribir"
    assert cuadro.tabla.item(1, 6).text() == "sin matricula"
    assert cuadro.tabla.item(2, 6).text() == "Escrita"


# ── la vista previa de los batches ─────────────────────────────────

def test_una_fila_por_batch_con_sus_cuentas(app):
    previstos = [
        previsto("DP | BITS -1", [separador(1), bitacora(2), bitacora(3)]),
        previsto("DP | BITS -2", [bitacora(1)]),
    ]

    cuadro = VistaPreviaBatches(previstos)

    assert cuadro.tabla.rowCount() == 2
    assert cuadro.tabla.item(0, 0).text() == "DP | BITS -1"
    assert cuadro.tabla.item(0, 1).text() == "3", "páginas, separador incluido"
    assert cuadro.tabla.item(0, 2).text() == "2", "bitácoras, separador aparte"


def test_el_que_todavia_no_existe_dice_que_esta_por_subir(app):
    previstos = [
        previsto("DP | BITS -1", [bitacora(1)]),
        previsto(
            "DP | BITS -2", [bitacora(1)],
            existe=True, subido=True, estado="Indexado: 8 de 8",
        ),
    ]

    cuadro = VistaPreviaBatches(previstos)

    assert cuadro.tabla.item(0, 3).text() == "Por subir"
    assert cuadro.tabla.item(1, 3).text() == "Indexado: 8 de 8"


def test_elegir_un_batch_habilita_ver_sus_bitacoras(app):
    cuadro = VistaPreviaBatches([previsto("DP | BITS", [bitacora(1)])])

    # Se abre con el primero elegido: no hay que adivinar que hay que
    # pulsar una fila antes de que el botón sirva para algo.
    assert cuadro.tabla.currentRow() == 0
    assert cuadro.boton_bitacoras.isEnabled()
    assert cuadro._elegido().nombre == "DP | BITS"


def test_sin_batches_no_hay_nada_que_abrir(app):
    cuadro = VistaPreviaBatches([])

    assert cuadro.tabla.rowCount() == 0
    assert not cuadro.boton_bitacoras.isEnabled()

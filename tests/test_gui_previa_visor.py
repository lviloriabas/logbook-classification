"""La lista de bitácoras de un batch se mira como el visor de CSV.

Comprobar que una bitácora concreta cae donde se espera obligaba a abrir el
PDF por fuera y contar páginas a mano. Aquí la hoja escaneada está al lado
de la fila, se busca por cualquier dato y las columnas se ordenan con un
clic, que es lo mismo que ya se hace en el visor de CSV.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.airvault.model import Registro
from app.gui.airvault_previa import BitacorasDelBatch, VistaPreviaBatches
from app.airvault.flujo import BatchPrevisto


#: Columna «Log Page» de la lista de páginas de un batch.
LOG_PAGE = 3


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def sin_rasterizar(monkeypatch):
    """Ni abre PDF ni los dibuja: aquí se prueba la ventana, no el render."""
    import numpy as np
    from app.vision import pdf_loader

    monkeypatch.setattr(pdf_loader, "page_count", lambda _path: 9)
    monkeypatch.setattr(
        pdf_loader,
        "render_page",
        lambda _path, _page, dpi=150: np.zeros((30, 20, 3), dtype=np.uint8),
    )


def _bitacora(seq, pagina, log, matricula="HP-1848CMP"):
    return Registro(
        seq=seq,
        archivo_origen="paginas.pdf",
        pagina_origen=pagina,
        matricula=matricula,
        log_number=log,
        flight_number="703",
        fecha="2026/08/11",
    )


def _ejecucion(tmp_path: Path) -> Path:
    """Una ejecución con su CSV, su JSON compañero y su PDF de origen."""
    run = tmp_path / "BITS 11 AUG 2026 09 00"
    datos = run / "datos"
    datos.mkdir(parents=True)
    origen = run / "paginas.pdf"
    origen.write_text("%PDF-1.4\n", encoding="utf-8")
    csv = datos / "BITS 11 AUG 2026 09 00.csv"
    csv.write_text(
        "file,page,log_number\n"
        "paginas.pdf,4,2271620\n"
        "paginas.pdf,7,2271621\n",
        encoding="utf-8",
    )
    csv.with_suffix(".json").write_text(
        json.dumps(
            {
                "reportes": [
                    {
                        "pdf_path": str(origen),
                        "pages": [{"page_number": 4}, {"page_number": 7}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return csv


def _cuadro(tmp_path: Path) -> BitacorasDelBatch:
    csv = _ejecucion(tmp_path)
    return BitacorasDelBatch(
        "DP | BITS",
        [_bitacora(1, 4, "2271620"), _bitacora(2, 7, "2271621", "HP-1849CMP")],
        csv=csv,
    )


def test_elegir_una_bitacora_abre_su_hoja_escaneada(
    app, tmp_path: Path, sin_rasterizar
):
    cuadro = _cuadro(tmp_path)
    try:
        # La primera fila queda elegida al abrir, con su página puesta.
        assert cuadro.visor._page == 4

        cuadro.tabla.selectRow(1)
        assert cuadro.visor._page == 7
        assert cuadro.visor._path.name == "paginas.pdf"
    finally:
        cuadro.visor.shutdown()
        cuadro.close()
        app.processEvents()


def test_el_doble_clic_vuelve_a_traer_la_hoja_de_la_fila(
    app, tmp_path: Path, sin_rasterizar
):
    """Aunque ya fuera la fila activa: se navegó el PDF y hay que volver."""
    cuadro = _cuadro(tmp_path)
    try:
        cuadro.tabla.selectRow(1)
        cuadro.visor.show_page(1)
        assert cuadro.visor._page == 4

        cuadro.tabla.itemDoubleClicked.emit(cuadro.tabla.item(1, 0))

        assert cuadro.visor._page == 7
    finally:
        cuadro.visor.shutdown()
        cuadro.close()
        app.processEvents()


def test_ordenar_por_una_columna_no_despista_al_visor(
    app, tmp_path: Path, sin_rasterizar
):
    """La fila lleva encima a qué bitácora corresponde, esté donde esté."""
    cuadro = _cuadro(tmp_path)
    try:
        # Log Page, de mayor a menor: la segunda bitácora pasa a la primera.
        cuadro.orden.cycle_column(LOG_PAGE)
        assert cuadro.tabla.item(0, LOG_PAGE).text() == "2271621"

        cuadro.tabla.selectRow(0)
        assert cuadro.visor._page == 7
    finally:
        cuadro.visor.shutdown()
        cuadro.close()
        app.processEvents()


def test_la_separadora_no_deja_a_la_vista_la_hoja_de_otra(
    app, tmp_path: Path, sin_rasterizar
):
    """No sale de ningún escaneo, y el panel tiene que decirlo.

    Su fila entra igual en la secuencia del visor: es lo que mantiene la
    numeración del panel pegada a la del batch, que es la que se busca en
    Web Index.
    """
    csv = _ejecucion(tmp_path)
    cuadro = BitacorasDelBatch(
        "DP | BITS",
        [
            Registro(seq=1, separador="HP-1848CMP"),
            _bitacora(2, 4, "2271620"),
            _bitacora(3, 7, "2271621"),
        ],
        csv=csv,
    )
    try:
        # Se abre en la separadora, que es la primera página del batch.
        assert cuadro.visor._path is None
        assert "hoja escaneada" in cuadro.visor.image.text()

        cuadro.tabla.selectRow(1)
        assert cuadro.visor._page == 4
        # Segunda página del batch, no primera bitácora de la lista.
        assert cuadro.visor._global_index == 2
    finally:
        cuadro.visor.shutdown()
        cuadro.close()
        app.processEvents()


def test_el_buscador_lleva_a_la_bitacora_y_a_su_hoja(
    app, tmp_path: Path, sin_rasterizar
):
    cuadro = _cuadro(tmp_path)
    try:
        cuadro.buscar_edit.setText("HP-1849CMP")
        cuadro._buscar()

        assert cuadro.tabla.currentRow() == 1
        assert cuadro.visor._page == 7
        assert "Coincidencia 1 de 1" in cuadro.busqueda_ayuda.text()

        cuadro.buscar_edit.setText("XX-0000")
        cuadro._buscar()
        assert "sin coincidencias" in cuadro.busqueda_ayuda.text()
    finally:
        cuadro.visor.shutdown()
        cuadro.close()
        app.processEvents()


def test_sin_csv_la_lista_sigue_sirviendo(app, tmp_path: Path):
    """Un batch cuyo manifiesto no dice de qué ejecución salió."""
    cuadro = BitacorasDelBatch(
        "DP | BITS", [_bitacora(1, 4, "2271620")], csv=""
    )
    try:
        assert cuadro.tabla.rowCount() == 1
        # El panel lo dice en vez de quedarse en blanco.
        assert cuadro.visor.image.text()
    finally:
        cuadro.visor.shutdown()
        cuadro.close()
        app.processEvents()


def test_la_lista_de_batches_tambien_se_busca_y_se_ordena(app):
    previstos = [
        BatchPrevisto(
            nombre="DP | BITS 2 de 2",
            parte=2,
            partes=2,
            revisar=False,
            pdf="entrega.pdf",
            registros=[_bitacora(1, 1, "2271620")],
        ),
        BatchPrevisto(
            nombre="DP | BITS REVISAR",
            parte=1,
            partes=1,
            revisar=True,
            pdf="revisar.pdf",
            registros=[
                _bitacora(1, 2, "2271621"),
                _bitacora(2, 3, "2271622"),
            ],
        ),
    ]
    cuadro = VistaPreviaBatches(previstos)
    try:
        cuadro.buscar_edit.setText("REVISAR")
        cuadro._buscar()
        assert cuadro.tabla.currentRow() == 1
        assert cuadro._elegido().nombre == "DP | BITS REVISAR"

        # Bitácoras, de mayor a menor: el de REVISAR lleva dos y sube.
        cuadro.orden.cycle_column(2)
        assert cuadro.tabla.item(0, 0).text() == "DP | BITS REVISAR"
        # Y elegir esa fila sigue dando el batch correcto.
        cuadro.tabla.selectRow(0)
        assert cuadro._elegido().nombre == "DP | BITS REVISAR"
    finally:
        cuadro.close()
        app.processEvents()

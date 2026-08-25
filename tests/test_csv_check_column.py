"""La casilla del visor de CSV vive en su propia columna.

Antes se colgaba de la primera columna de datos («file»), así que la marca y
el nombre del archivo compartían celda: no se veía qué era cada cosa y
ordenar por esa columna movía las marcas junto al texto. Ahora es una
columna aparte, siempre visible, que la vista resumida no oculta.

De paso se comprueba lo que hace lenta a esta ventana: la tabla lee del CSV
que ya está en memoria en vez de guardar un ítem por celda, y ordenar es
reordenar índices.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from app.gui.csv_model import CHECK_COLUMN
from app.gui.csv_viewer import CsvViewerWindow


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _visor(tmp_path: Path) -> CsvViewerWindow:
    run = tmp_path / "run"
    datos = run / "datos"
    datos.mkdir(parents=True)
    (datos / "run.csv").write_text(
        "file,page,log_number,matricula\n"
        "a.pdf,1,2271620,HP-1848CMP\n"
        "b.pdf,2,2271621,HP-1849CMP\n"
        "c.pdf,3,2271622,HP-1850CMP\n",
        encoding="utf-8",
    )
    visor = CsvViewerWindow(tmp_path)
    assert visor.load_folder(run)
    return visor


def test_la_casilla_no_comparte_columna_con_ningun_dato(app, tmp_path: Path):
    visor = _visor(tmp_path)
    try:
        modelo = visor.table_model

        # La primera columna es solo la casilla: sin rótulo y sin texto.
        assert modelo.headerData(
            CHECK_COLUMN, Qt.Orientation.Horizontal
        ) == ""
        assert modelo.index(0, CHECK_COLUMN).data() is None
        assert (
            modelo.index(0, CHECK_COLUMN).data(Qt.ItemDataRole.CheckStateRole)
            is Qt.CheckState.Unchecked
        )
        # Y el primer dato del CSV empieza en la siguiente.
        assert modelo.index(0, 1).data() == "a.pdf"
        assert modelo.column_of("file") == 1

        # Solo esa columna se marca; las de datos no son marcables.
        marcable = Qt.ItemFlag.ItemIsUserCheckable
        assert modelo.flags(modelo.index(0, CHECK_COLUMN)) & marcable
        assert not modelo.flags(modelo.index(0, 1)) & marcable
    finally:
        visor.pdf_viewer.shutdown()
        visor.close()
        app.processEvents()


def test_la_columna_de_casillas_no_se_oculta_en_la_vista_resumida(
    app, tmp_path: Path
):
    """Elegir páginas sueltas no puede depender de qué columnas se vean."""
    visor = _visor(tmp_path)
    try:
        visor.column_toggle.setChecked(True)
        assert not visor.table.isColumnHidden(CHECK_COLUMN)

        visor.column_toggle.setChecked(False)
        assert not visor.table.isColumnHidden(CHECK_COLUMN)
    finally:
        visor.pdf_viewer.shutdown()
        visor.close()
        app.processEvents()


def test_lo_marcado_manda_sobre_lo_resaltado(app, tmp_path: Path):
    """La marca sobrevive a recorrer la tabla; el resalte no."""
    visor = _visor(tmp_path)
    try:
        visor.table.selectRow(0)
        assert visor._selected_source_rows() == [0]

        visor.table_model.toggle_rows([2])
        # Con algo marcado, el resalte deja de decidir qué se elimina.
        assert visor._checked_source_rows() == [2]
        assert visor._selected_source_rows() == [2]

        visor.table_model.toggle_rows([2])
        assert visor._selected_source_rows() == [0]
    finally:
        visor.pdf_viewer.shutdown()
        visor.close()
        app.processEvents()


def test_ordenar_no_despega_la_marca_de_su_bitacora(app, tmp_path: Path):
    """La marca es de la página del CSV, no de la posición en pantalla."""
    visor = _visor(tmp_path)
    try:
        modelo = visor.table_model
        visor.table_model.toggle_rows([0])  # a.pdf, la primera del CSV

        visor.table_sort.cycle_column(modelo.column_of("log_number"))
        assert modelo.index(0, modelo.column_of("file")).data() == "c.pdf"

        # a.pdf está ahora abajo del todo y sigue siendo la marcada.
        assert visor._checked_source_rows() == [0]
        assert (
            modelo.index(2, CHECK_COLUMN).data(Qt.ItemDataRole.CheckStateRole)
            is Qt.CheckState.Checked
        )
        assert (
            modelo.index(0, CHECK_COLUMN).data(Qt.ItemDataRole.CheckStateRole)
            is Qt.CheckState.Unchecked
        )
    finally:
        visor.pdf_viewer.shutdown()
        visor.close()
        app.processEvents()


def test_la_barra_marca_de_una_vez_las_filas_elegidas(app, tmp_path: Path):
    """Con una selección larga, apuntar casilla por casilla no es opción."""
    visor = _visor(tmp_path)
    try:
        visor.table.selectAll()
        visor._toggle_checked_rows()
        assert visor._checked_source_rows() == [0, 1, 2]

        # Repetirlo las desmarca todas, no las alterna una a una.
        visor._toggle_checked_rows()
        assert visor._checked_source_rows() == []
    finally:
        visor.pdf_viewer.shutdown()
        visor.close()
        app.processEvents()


def test_la_tabla_delega_en_el_csv_en_vez_de_copiarlo(app, tmp_path: Path):
    """Ordenar es reordenar índices: no se mueve ninguna celda de sitio."""
    visor = _visor(tmp_path)
    try:
        modelo = visor.table_model
        # Las filas del modelo son las del CSV, no copias suyas: la tabla
        # no duplica en memoria lo que ya está leído.
        assert all(
            fila is original
            for fila, original in zip(modelo.rows, visor._rows)
        )

        columna = modelo.column_of("page")
        visor.table_sort.cycle_column(columna)
        assert [
            modelo.source_row(fila) for fila in range(modelo.rowCount())
        ] == [2, 1, 0]
        # El CSV sigue en su orden: lo que cambió es cómo se mira.
        assert [fila["page"] for fila in visor._rows] == ["1", "2", "3"]
    finally:
        visor.pdf_viewer.shutdown()
        visor.close()
        app.processEvents()

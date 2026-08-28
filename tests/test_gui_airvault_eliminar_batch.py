"""Eliminar batches desde el clic derecho de la cola.

Cancelar y eliminar no son lo mismo, y la diferencia importa. Un batch
cancelado sigue en la cola con su ID y con sus bitácoras apuntadas, así que
ningún reparto posterior las vuelve a mandar. Eliminarlo borra esa memoria:
su manifiesto se va a la Papelera, su anotación sale del registro de la
entrega y sus bitácoras vuelven a quedar libres para el reparto siguiente.

Lo que ya esté en AirVault no se toca: eso no vive aquí, y el batch remoto
se queda donde estaba.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

from app.airvault import registro
from app.airvault.config import AirVaultConfig
from app.airvault.flujo import estado_local, preparar_partes
from app.airvault.manifest import ruta_manifiesto
from app.gui.airvault_window import AirVaultWindow

from test_airvault_entrega import corrida


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def papelera(monkeypatch):
    """Recoge lo que se mandaría a la Papelera y lo borra de verdad."""
    import shutil

    enviados: list[Path] = []

    def enviar(rutas):
        enviados.extend(rutas)
        for ruta in rutas:
            if ruta.is_dir():
                shutil.rmtree(ruta)
            else:
                ruta.unlink()
        return list(rutas), []

    monkeypatch.setattr("app.gui.airvault_window.send_to_trash", enviar)
    return enviados


@pytest.fixture
def dice_que_si(monkeypatch):
    monkeypatch.setattr(
        "app.gui.airvault_window.QMessageBox.warning",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )


def repartida(tmp_path, paginas_por_batch=5):
    """Una ejecución ya repartida en varios batches, con su registro."""
    csv_path, _partes = corrida(tmp_path)
    carpeta = tmp_path / "output" / "airvault" / "BITS 19 AUG 2026 10 00"
    trabajos = preparar_partes(
        AirVaultConfig(), carpeta, csv_path,
        paginas_por_batch=paginas_por_batch,
    )
    return csv_path, carpeta, trabajos


def cargada(tmp_path, trabajos):
    """La ventana con esos trabajos en la cola, sin tocar el disco de más."""
    ventana = AirVaultWindow(tmp_path)
    ventana._trabajos = list(trabajos)
    ventana._estados = [estado_local(trabajo) for trabajo in trabajos]
    ventana._pintar_lotes()
    return ventana


def test_el_menu_de_la_cola_ofrece_eliminar_el_batch(app, tmp_path):
    _csv, _carpeta, trabajos = repartida(tmp_path)
    ventana = cargada(tmp_path, trabajos)
    try:
        menu = ventana._acciones_de_la_cola(ventana._estados[:1])
        acciones = {
            accion.text(): accion for accion in menu.actions()
            if not accion.isSeparator()
        }

        assert acciones["Eliminar el batch…"].isEnabled()
        # Cancelar sigue estando: son dos cosas distintas y se ofrecen las dos.
        assert "Cancelar en la cola" in acciones
    finally:
        ventana.close()
        app.processEvents()


def test_eliminar_manda_el_batch_a_la_papelera_y_lo_saca_de_la_cola(
    app, tmp_path, papelera, dice_que_si,
):
    _csv, carpeta, trabajos = repartida(tmp_path)
    assert len(trabajos) > 1, "hacen falta varias partes para esta prueba"
    ventana = cargada(tmp_path, trabajos)
    try:
        fuera = ventana._estados[0]
        suya = Path(fuera.trabajo.carpeta)

        ventana._eliminar_estas([fuera])

        # Cada parte vive en su propia carpeta, así que se va entera con el
        # PDF que se había preparado para subirla.
        assert papelera == [suya]
        assert not suya.exists()
        assert ventana.lotes.rowCount() == len(trabajos) - 1
        assert fuera.trabajo not in ventana._trabajos
        # Y las demás siguen donde estaban.
        assert ruta_manifiesto(trabajos[1].carpeta).is_file()
    finally:
        ventana.close()
        app.processEvents()


def test_eliminar_libera_las_bitacoras_para_el_proximo_reparto(
    app, tmp_path, papelera, dice_que_si,
):
    """La diferencia con cancelar: aquí las páginas vuelven a repartirse."""
    _csv, carpeta, trabajos = repartida(tmp_path)
    ventana = cargada(tmp_path, trabajos)
    try:
        fuera = ventana._estados[0]
        suyas = {
            (r.archivo_origen, int(r.pagina_origen))
            for r in fuera.trabajo.manifiesto.registros
            if not r.es_separador and r.archivo_origen
        }
        assert suyas <= registro.leer(carpeta).anotadas()

        ventana._eliminar_estas([fuera])

        anotadas = registro.leer(carpeta).anotadas()
        assert not (suyas & anotadas)
        # Lo de los demás batches sigue anotado: se olvida uno, no la entrega.
        assert anotadas
    finally:
        ventana.close()
        app.processEvents()


def test_eliminar_varios_a_la_vez_se_los_lleva_todos(
    app, tmp_path, papelera, dice_que_si,
):
    """La cola deja elegir varias filas y la acción vale para todas."""
    _csv, _carpeta, trabajos = repartida(tmp_path)
    assert len(trabajos) > 2, "hacen falta al menos tres partes"
    ventana = cargada(tmp_path, trabajos)
    try:
        elegidos = ventana._estados[:2]

        ventana._eliminar_estas(elegidos)

        assert len(papelera) == 2
        assert ventana.lotes.rowCount() == len(trabajos) - 2
    finally:
        ventana.close()
        app.processEvents()


def test_no_se_elimina_si_se_dice_que_no(app, tmp_path, papelera, monkeypatch):
    monkeypatch.setattr(
        "app.gui.airvault_window.QMessageBox.warning",
        lambda *args, **kwargs: QMessageBox.StandardButton.No,
    )
    _csv, _carpeta, trabajos = repartida(tmp_path)
    ventana = cargada(tmp_path, trabajos)
    try:
        ventana._eliminar_estas(ventana._estados[:1])

        assert papelera == []
        assert ventana.lotes.rowCount() == len(trabajos)
    finally:
        ventana.close()
        app.processEvents()


def test_no_se_elimina_nada_mientras_la_ventana_trabaja(
    app, tmp_path, papelera, dice_que_si, monkeypatch,
):
    _csv, _carpeta, trabajos = repartida(tmp_path)
    ventana = cargada(tmp_path, trabajos)
    try:
        monkeypatch.setattr(type(ventana), "hilo", lambda self: object())
        avisos = []
        monkeypatch.setattr(
            "app.gui.airvault_window.QMessageBox.information",
            lambda *args, **kwargs: avisos.append(args[2]),
        )

        ventana._eliminar_estas(ventana._estados[:1])

        assert papelera == []
        assert ventana.lotes.rowCount() == len(trabajos)
        assert avisos and "Cancele el trabajo" in avisos[0]
    finally:
        monkeypatch.undo()
        ventana.close()
        app.processEvents()


def test_un_batch_de_fuera_de_la_carpeta_de_trabajos_no_se_elimina(
    app, tmp_path, papelera, dice_que_si, monkeypatch,
):
    """Sacarlo de la cola sin borrarlo lo devolvería al recargar."""
    _csv, _carpeta, trabajos = repartida(tmp_path)
    ventana = cargada(tmp_path, trabajos)
    try:
        ajeno = ventana._estados[0]
        ajeno.trabajo.carpeta = tmp_path / "por_ahi" / "job"
        avisos = []
        monkeypatch.setattr(
            "app.gui.airvault_window.QMessageBox.information",
            lambda *args, **kwargs: avisos.append(args[2]),
        )

        ventana._eliminar_estas([ajeno])

        assert papelera == []
        assert ventana.lotes.rowCount() == len(trabajos)
        assert avisos and "carpeta de trabajos" in avisos[0]
    finally:
        ventana.close()
        app.processEvents()


def test_sin_repartir_solo_se_va_el_manifiesto_del_batch(
    app, tmp_path, papelera, dice_que_si,
):
    """El batch único vive en la carpeta de la entrega, que es de todos.

    Ahí no se puede llevar la carpeta: dentro está el registro de la entrega
    y los manifiestos apartados de repartos anteriores, que no son suyos.
    """
    _csv, carpeta, trabajos = repartida(tmp_path, paginas_por_batch=0)
    assert len(trabajos) == 1
    assert Path(trabajos[0].carpeta) == carpeta
    ventana = cargada(tmp_path, trabajos)
    try:
        ventana._eliminar_estas(ventana._estados[:1])

        assert papelera == [ruta_manifiesto(carpeta)]
        assert carpeta.is_dir()
        assert registro.ruta_registro(carpeta).is_file()
        assert ventana.lotes.rowCount() == 0
    finally:
        ventana.close()
        app.processEvents()

"""La ventana «Indexar en AirVault».

Comprueba lo que la ventana promete: que el historial enseñe las
ejecuciones y diga cuáles se pueden subir, que subir y indexar sean dos
tiempos separados por la espera de AirVault, que esa espera se pregunte
sola sin congelar nada, y que cerrar el programa espere al lote a medio
escribir.

Nada de esto toca la red: la ventana solo prepara el trabajo y arranca el
hilo, y lo que decide si una página se escribe vive en ``app.airvault``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QLineEdit, QMessageBox

from app.gui.airvault_window import (
    ANCHO_MAXIMO_NOMBRE_BATCH,
    ANCHO_MINIMO_NOMBRE_BATCH,
    COLOR_INDEXADO,
    AirVaultWindow,
    csv_de_corrida,
)

RAIZ = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def ventana(app):
    return AirVaultWindow(RAIZ)


def corrida(
    raiz, nombre="BITS 18 AUG 2026 05 42", exportada=True, paginas=19,
):
    """Deja en ``raiz/output`` una corrida como la que escribe la exportación.

    Sin ``exportada`` queda solo lo que produce el procesamiento: los datos,
    sin PDF de entrega ni índice de páginas, que es lo que hace falta para
    subirla.
    """
    carpeta = raiz / "output" / nombre
    (carpeta / "datos").mkdir(parents=True)
    csv = carpeta / "datos" / f"{nombre}.CSV"
    csv.write_text("file,page,log_number,matricula,date\n", encoding="utf-8")
    (carpeta / "stats.json").write_text(
        json.dumps({"corrida": nombre, "total_paginas": paginas}),
        encoding="utf-8",
    )
    if exportada:
        (carpeta / f"{nombre}.pdf").write_bytes(b"%PDF-1.4\n")
        csv.with_name(f"{csv.stem}_paginas.json").write_text(
            json.dumps([{"pdf": f"{nombre}.pdf", "paginas": [1]}]),
            encoding="utf-8",
        )
    return csv


def registrar_en_airvault(raiz: Path, csv: Path) -> Path:
    """Deja el manifiesto local mínimo de la ejecución seleccionada."""
    from app.airvault.flujo import carpeta_de_corrida, carpeta_de_trabajo
    from app.airvault.manifest import guardar
    from app.airvault.model import Manifiesto

    nombre = carpeta_de_corrida(csv).name
    carpeta = raiz / carpeta_de_trabajo(nombre)
    pdf = csv.parent.parent / f"{nombre}.pdf"
    manifiesto = Manifiesto(
        job_id=nombre,
        nombre_batch=f"DP | {nombre}",
        csv_origen=str(csv.resolve()),
        pdf_origen=str(pdf.resolve()),
    )
    return guardar(manifiesto, carpeta)


# ── forma de la ventana ────────────────────────────────────────────

def test_es_una_ventana_aparte_y_arranca_escondida(ventana):
    """Empotrada le quitaba alto a la vista previa y rompía el reparto."""
    assert ventana.isHidden()
    assert ventana.windowTitle() == "Indexar en AirVault"


def test_la_cookie_no_se_ve_al_teclearla(ventana):
    """Vale tanto como la contraseña: no puede quedar a la vista."""
    assert ventana.cookie_edit.echoMode() == QLineEdit.EchoMode.Password


def test_indexar_esta_apagado_hasta_que_haya_un_lote_listo(ventana):
    assert not ventana.boton_indexar.isEnabled()
    assert not ventana.boton_reporte.isEnabled()


def test_sin_corrida_elegida_no_hay_nada_que_subir(ventana):
    assert not ventana.boton_subir.isEnabled()


def test_sin_nada_subido_no_hay_nada_que_comprobar(ventana):
    """Comprobar es preguntar por lotes; sin subir no hay ninguno."""
    assert not ventana.boton_comprobar.isEnabled()


def test_sin_registro_no_hay_nada_que_eliminar(ventana):
    assert not ventana.boton_eliminar_registro.isEnabled()


def test_completar_el_batch_no_viene_marcado(ventana):
    """Cerrar el lote lo saca de la cola: eso se pide a propósito."""
    assert not ventana.completar_check.isChecked()


def test_el_limite_de_quick_upload_empieza_en_300_paginas(ventana):
    assert ventana.limite_batch_spin.value() == 300


def test_la_espera_automatica_empieza_en_dos_minutos(ventana):
    assert ventana.minutos_spin.value() == 2


def test_el_menu_de_automatizacion_empieza_oculto_y_es_secuencial(ventana):
    assert ventana.menu_automatizacion.isHidden()
    assert ventana.auto_subir_check.isChecked()
    assert ventana.auto_esperar_check.isChecked()
    assert not ventana.auto_indexar_check.isChecked()
    assert not ventana.auto_completar_check.isEnabled()

    ventana.auto_indexar_check.setChecked(True)
    assert ventana.auto_completar_check.isEnabled()
    ventana.auto_esperar_check.setChecked(False)
    assert not ventana.auto_indexar_check.isChecked()
    assert not ventana.auto_indexar_check.isEnabled()
    assert not ventana.auto_completar_check.isEnabled()


def test_la_ventana_usa_batch_en_sus_campos_y_tabla(ventana):
    assert [
        ventana.lotes.horizontalHeaderItem(columna).text()
        for columna in range(ventana.lotes.columnCount())
    ] == ["ID", "Batch", "Páginas", "Estado"]
    assert "batch" in ventana.lote_edit.placeholderText().lower()


def test_revisar_airvault_esta_a_la_derecha_de_subir(ventana):
    botones = ventana.layout().itemAt(ventana.layout().count() - 1).layout()
    widgets = [
        botones.itemAt(indice).widget()
        for indice in range(botones.count())
        if botones.itemAt(indice).widget() is not None
    ]

    assert ventana.boton_revisar.text() == "Revisar en AirVault"
    assert widgets.index(ventana.boton_revisar) == (
        widgets.index(ventana.boton_subir) + 1
    )


def test_el_usuario_puede_elegir_el_limite_antes_de_subir(ventana, tmp_path):
    ventana.fijar_corrida(corrida(tmp_path))
    ventana.limite_batch_spin.setValue(450)

    estado = ventana._base_del_estado()

    assert estado["paginas_por_batch"] == 450


# ── el historial ───────────────────────────────────────────────────

def test_el_historial_lista_las_corridas_de_la_mas_reciente(app, tmp_path):
    corrida(tmp_path, "BITS 17 AUG 2026 05 50")
    corrida(tmp_path, "BITS 18 AUG 2026 05 42")
    ventana = AirVaultWindow(tmp_path)
    ventana._refrescar_historial()
    nombres = [
        ventana.historial.item(fila, 0).text()
        for fila in range(ventana.historial.rowCount())
    ]
    assert nombres == ["BITS 18 AUG 2026 05 42", "BITS 17 AUG 2026 05 50"]


def test_el_historial_cuenta_paginas_y_entrega(app, tmp_path):
    corrida(tmp_path, paginas=19)
    ventana = AirVaultWindow(tmp_path)
    ventana._refrescar_historial()
    assert ventana.historial.item(0, 1).text() == "19"
    assert ventana.historial.item(0, 2).text() == "1 archivo"


def test_elegir_del_historial_apunta_a_esa_corrida(app, tmp_path):
    corrida(tmp_path, "BITS 17 AUG 2026 05 50")
    csv = corrida(tmp_path, "BITS 18 AUG 2026 05 42")
    ventana = AirVaultWindow(tmp_path)
    ventana._refrescar_historial()
    ventana.historial.selectRow(0)
    assert ventana.corrida_edit.text() == str(csv)
    assert ventana.lote_edit.text() == "DP | BITS 18 AUG 2026 05 42"


def test_sin_elegir_nada_se_propone_la_mas_reciente(app, tmp_path):
    corrida(tmp_path, "BITS 17 AUG 2026 05 50")
    csv = corrida(tmp_path, "BITS 18 AUG 2026 05 42")
    ventana = AirVaultWindow(tmp_path)
    ventana._refrescar_historial()
    assert ventana.corrida_edit.text() == str(csv)


def test_se_propone_la_exportada_aunque_no_sea_la_ultima(app, tmp_path):
    """Procesar sin exportar es normal; abrir señalando esa corrida, no."""
    csv = corrida(tmp_path, "BITS 17 AUG 2026 05 50")
    corrida(tmp_path, "BITS 18 AUG 2026 05 42", exportada=False)
    ventana = AirVaultWindow(tmp_path)
    ventana._refrescar_historial()
    assert ventana.corrida_edit.text() == str(csv)
    assert ventana.boton_subir.isEnabled()


def test_una_corrida_sin_exportar_se_ve_pero_no_se_sube(app, tmp_path):
    """Sin PDF de entrega no hay nada que subir, y hay que decir por qué."""
    corrida(tmp_path, exportada=False)
    ventana = AirVaultWindow(tmp_path)
    ventana._refrescar_historial()
    assert ventana.historial.item(0, 2).text() == "Sin exportar"
    assert not ventana.boton_subir.isEnabled()
    assert "Expórtela" in ventana.resumen.text()


def test_una_corrida_exportada_se_puede_subir(app, tmp_path):
    corrida(tmp_path)
    ventana = AirVaultWindow(tmp_path)
    ventana._refrescar_historial()
    assert ventana.boton_subir.isEnabled()
    assert "Sin subir" in ventana.resumen.text()


def test_sin_corridas_procesadas_lo_dice(app, tmp_path):
    """Una tabla vacía no dice nada; así se lee que aún no hay nada."""
    ventana = AirVaultWindow(tmp_path)
    ventana._refrescar_historial()
    assert ventana.historial.rowCount() == 0
    assert "No hay ejecuciones procesadas" in ventana.resumen.text()


def test_el_csv_que_se_sube_es_el_minimo_no_el_completo(tmp_path):
    """El ``_completo`` trae el detalle de lectura; AirVault no lo entiende."""
    csv = corrida(tmp_path)
    csv.with_name(f"{csv.stem}_completo.CSV").write_text("x\n", encoding="utf-8")
    assert csv_de_corrida(csv.parent.parent) == csv


# ── la corrida elegida ─────────────────────────────────────────────

def test_apunta_a_la_corrida_y_propone_el_nombre_del_lote(ventana, tmp_path):
    csv = corrida(tmp_path)
    ventana.fijar_corrida(csv)
    assert ventana.corrida_edit.text() == str(csv)
    assert ventana.lote_edit.text() == "DP | BITS 18 AUG 2026 05 42"


def test_el_campo_de_la_corrida_no_se_teclea(ventana):
    """Se elige en el historial o con «Otra ejecución…»."""
    assert ventana.corrida_edit.isReadOnly()


def test_cambiar_de_corrida_tira_lo_que_se_sabia_de_la_anterior(ventana,
                                                                tmp_path):
    """Los lotes de una ejecución no dicen nada de los de otra."""
    ventana._estado = {"planes": {"x": 1}}
    ventana.boton_indexar.setEnabled(True)
    ventana.fijar_corrida(corrida(tmp_path))
    assert ventana._estado == {}
    assert ventana._estados == []
    # Indexar tambien sirve para conectarse y recuperar batches que esta
    # aplicacion hubiera subido en una ejecucion anterior.
    assert ventana.boton_indexar.isEnabled()


def test_eliminar_registro_reinicia_solo_el_estado_local(
    app, tmp_path, monkeypatch
):
    csv = corrida(tmp_path)
    pdf = csv.parent.parent / f"{csv.parent.parent.name}.pdf"
    manifiesto = registrar_en_airvault(tmp_path, csv)
    ventana = AirVaultWindow(tmp_path)
    ventana.fijar_corrida(csv)
    enviados = []

    def papelera(rutas):
        enviados.extend(rutas)
        for ruta in rutas:
            ruta.unlink()
        return list(rutas), []

    monkeypatch.setattr(
        "app.gui.airvault_window.send_to_trash", papelera
    )
    monkeypatch.setattr(
        "app.gui.airvault_window.QMessageBox.warning",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )

    assert ventana.boton_eliminar_registro.isEnabled()
    ventana._eliminar_registro()

    assert enviados == [manifiesto]
    assert not manifiesto.exists()
    assert csv.exists() and pdf.exists()
    assert ventana._trabajos == []
    assert ventana.lotes.rowCount() == 0
    assert not ventana.boton_eliminar_registro.isEnabled()
    assert "batches remotos no se modificaron" in ventana.resumen.text()


# ── no se arranca sin lo imprescindible ────────────────────────────

def test_sin_corrida_no_arranca_nada(ventana):
    ventana._subir()
    assert ventana._worker is None
    assert "Falta elegir la ejecución" in ventana.resumen.text()


def test_sin_nombre_de_lote_no_arranca_nada(ventana, tmp_path):
    ventana.fijar_corrida(corrida(tmp_path))
    ventana.lote_edit.setText("   ")
    ventana._subir()
    assert ventana._worker is None
    assert "Falta el nombre del batch" in ventana.resumen.text()


def test_indexar_sin_ningun_lote_listo_no_hace_nada(ventana):
    ventana._indexar()
    assert ventana._worker is None


def test_indexar_termina_primero_todas_las_subidas(ventana, monkeypatch):
    class ManifiestoPendiente:
        def etapa_hecha(self, nombre):
            return nombre != "subir"

    class TrabajoPendiente:
        manifiesto = ManifiestoPendiente()

    continuaciones = []
    ventana._trabajos = [TrabajoPendiente()]
    monkeypatch.setattr(
        ventana, "_continuar_pendiente", lambda: continuaciones.append(True)
    )

    ventana._indexar()

    assert continuaciones == [True]


# ── lo que cuenta al terminar ──────────────────────────────────────

class PlanFalso:
    batch_id = "003SRO"

    def resumen(self):
        return {"total": 5, "escribibles": 2, "bloqueadas": 1,
                "separadores": 2, "avisos_globales": 0}


class ManifiestoFalso:
    def __init__(self, nombre, paginas, batch_id):
        self.nombre_batch = nombre
        self.registros = [None] * paginas
        self.batch_id = batch_id


class TrabajoFalso:
    def __init__(self, nombre="DP | BITS", paginas=5, batch_id="003SRO",
                 carpeta="job"):
        self.manifiesto = ManifiestoFalso(nombre, paginas, batch_id)
        self.carpeta = carpeta


def parte(estado, nombre="DP | BITS", detalle="", carpeta="job"):
    """Una fila de la lista de lotes, como la devuelve el módulo."""
    from app.airvault.flujo import EstadoParte

    return EstadoParte(TrabajoFalso(nombre, carpeta=carpeta), estado, detalle)


def test_subir_no_indexa_nada_y_dice_que_falta_esperar(ventana):
    """Subir y estar listo son cosas distintas: entre medias está AirVault."""
    ventana._al_subir({"trabajos": [TrabajoFalso()], "cliente": object()})
    texto = ventana.resumen.text()
    assert "Subida terminada" in texto
    assert "procesar" in texto
    assert not ventana.boton_indexar.isEnabled()


def test_cada_click_en_subir_confirma_los_batches_en_airvault(
    ventana, monkeypatch
):
    """La marca local de subida no basta para habilitar el indexado."""
    comprobaciones = []
    monkeypatch.setattr(
        ventana, "_comprobar", lambda: comprobaciones.append(True)
    )
    ventana.auto_esperar_check.setChecked(False)
    ventana._comprobar_al_terminar = False

    ventana._al_subir({"trabajos": [TrabajoFalso()], "cliente": object()})
    ventana._al_terminar()

    assert comprobaciones == [True]
    assert "confirma cada batch" in ventana.bitacora.item(
        ventana.bitacora.count() - 1
    ).text()


def test_la_lista_dice_en_que_va_cada_lote(ventana):
    from app.airvault.flujo import LISTO, PROCESANDO

    ventana._estados = [
        parte(LISTO, "DP | BITS", "5 paginas"),
        parte(PROCESANDO, "DP | BITS REVISAR", "2 de 5 paginas",
              carpeta="revisar"),
    ]
    ventana._pintar_lotes()
    assert ventana.lotes.rowCount() == 2
    assert ventana.lotes.item(0, 0).text() == "003SRO"
    assert ventana.lotes.item(0, 1).text() == "DP | BITS"
    assert ventana.lotes.item(0, 2).text() == "5"
    assert "Listo para indexar" in ventana.lotes.item(0, 3).text()
    assert "Procesandose" in ventana.lotes.item(1, 3).text()


def test_el_id_solo_aparece_cuando_airvault_lo_encuentra(ventana):
    from app.airvault.flujo import BUSCANDO

    sin_encontrar = parte(BUSCANDO)
    sin_encontrar.trabajo.manifiesto.batch_id = None
    ventana._estados = [sin_encontrar]

    ventana._pintar_lotes()

    assert ventana.lotes.item(0, 0).text() == ""


def test_batch_conserva_el_nombre_hasta_un_ancho_razonable(ventana):
    from app.airvault.flujo import LISTO

    nombre = "DP | BIT 18 AUG 2026 05 42 - DIVISION PRINCIPAL"
    ventana._estados = [parte(LISTO, nombre)]

    ventana._pintar_lotes()

    assert ventana.lotes.item(0, 1).text() == nombre
    assert ventana.lotes.item(0, 1).toolTip() == nombre
    assert ventana.lotes.columnWidth(1) == min(
        max(
            ventana.lotes.sizeHintForColumn(1) + 16,
            ANCHO_MINIMO_NOMBRE_BATCH,
        ),
        ANCHO_MAXIMO_NOMBRE_BATCH,
    )


def test_todos_los_batches_confirmados_quedan_activos_en_blanco(ventana):
    from app.airvault.flujo import LISTO, SOLO_REVISAR

    ventana._estados = [
        parte(LISTO, "DP | BIT"),
        parte(LISTO, "DP | BIT -2", carpeta="parte-02"),
        parte(SOLO_REVISAR, "DP | BIT REVISAR", carpeta="revisar"),
    ]

    ventana._pintar_lotes()

    assert all(
        ventana.lotes.item(fila, 0).foreground().style()
        is Qt.BrushStyle.NoBrush
        for fila in range(ventana.lotes.rowCount())
    )


def test_un_batch_indexado_por_lo_menos_una_vez_queda_verde(ventana):
    from app.airvault.flujo import LISTO
    from app.airvault.model import EstadoEtapa, Etapa

    indexado = parte(LISTO)
    indexado.trabajo.manifiesto.etapas = {
        "indexar": Etapa(estado=EstadoEtapa.ERROR),
    }
    ventana._estados = [indexado]

    ventana._pintar_lotes()

    assert all(
        ventana.lotes.item(0, columna).foreground().color()
        == QColor(COLOR_INDEXADO)
        for columna in range(ventana.lotes.columnCount())
    )


def test_un_lote_listo_se_puede_indexar_y_dice_cuanto_escribiria(ventana):
    from app.airvault.flujo import LISTO

    listo = parte(LISTO)
    ventana._al_comprobar({
        "estados": [listo],
        "planes": {"job": (PlanFalso(), None)},
        "partes": [("DP | BIT 003SRO", PlanFalso())],
        "reporte": "r.html",
    })
    texto = ventana.resumen.text()
    assert "5 páginas" in texto and "2 se escribirían" in texto
    assert "Nada se ha escrito todavía" in texto
    assert ventana.boton_indexar.isEnabled()


def test_un_lote_listo_sin_plan_todavia_no_se_indexa(ventana):
    """El plan es lo que dice qué se escribiría; sin él no hay qué aprobar."""
    from app.airvault.flujo import LISTO

    ventana._al_comprobar({
        "estados": [parte(LISTO)], "planes": {}, "partes": [],
        "reporte": None,
    })
    assert not ventana.boton_indexar.isEnabled()


class ResultadoFalso:
    escritas, omitidas, fallidas = 2, 1, 0
    detalles: list = []
    interrumpido = ""


def test_al_indexar_cuenta_como_quedo_el_lote(ventana):
    ventana._al_indexar({"resultado": ResultadoFalso(), "validas": 2, "total": 3})
    texto = ventana.resumen.text()
    assert "Escritas 2" in texto and "2 de 3 páginas válidas" in texto


def test_un_indexado_cortado_dice_que_lo_que_falta_se_retoma(ventana):
    """Con la sesión o la red caídas, lo escrito no se pierde ni se repite."""
    class Cortado(ResultadoFalso):
        interrumpido = "La sesion de AirVault caduco."

    ventana._al_indexar({"resultado": Cortado(), "validas": 2, "total": 5})
    texto = ventana.resumen.text()
    assert "se cortó" in texto and "caduco" in texto
    assert "sin repetir lo escrito" in texto


# ── la espera de AirVault ──────────────────────────────────────────

def test_mientras_falte_un_lote_se_sigue_preguntando_solo(ventana):
    """Un lote tarda en salir de la cola; nadie va a estar pulsando."""
    from app.airvault.flujo import PROCESANDO

    ventana._al_comprobar({
        "estados": [parte(PROCESANDO, detalle="2 de 5 paginas")],
        "planes": {}, "partes": [], "reporte": None,
    })
    assert ventana._vigilante is not None
    assert ventana._vigilante.isActive()
    assert ventana._vigilante.interval() == 2 * 60_000


def test_cuando_no_queda_nada_que_esperar_deja_de_preguntar(ventana):
    """Ya listo, AirVault no va a cambiarlo solo: preguntar sobra."""
    from app.airvault.flujo import LISTO

    ventana._al_comprobar({
        "estados": [parte(LISTO)], "planes": {}, "partes": [],
        "reporte": None,
    })
    assert ventana._vigilante is None or not ventana._vigilante.isActive()


def test_sin_la_comprobacion_automatica_no_pregunta_sola(ventana):
    from app.airvault.flujo import PROCESANDO

    ventana._estados = [parte(PROCESANDO)]
    ventana._ajustar_vigilancia()
    assert ventana._vigilante.isActive()
    ventana.auto_check.setChecked(False)
    assert not ventana._vigilante.isActive()


def test_cerrar_la_ventana_no_apaga_la_comprobacion_automatica(ventana):
    """Esperar a AirVault lleva horas; lo normal es cerrarla mientras tanto."""
    from app.airvault.flujo import PROCESANDO

    ventana._estados = [parte(PROCESANDO)]
    ventana._ajustar_vigilancia()
    ventana.close()
    assert ventana._vigilante.isActive()


def test_el_reloj_no_pregunta_con_algo_en_vuelo(ventana):
    """Dos trabajos a la vez contra el mismo lote se estorban."""
    ventana._worker = HiloFalso()
    try:
        ventana._comprobar_solo()
        assert ventana._worker is not None
        assert isinstance(ventana._worker, HiloFalso)
    finally:
        ventana._worker = None


def test_un_fallo_para_la_comprobacion_automatica(ventana):
    """Repetirlo cada cinco minutos solo repetiría el mismo error."""
    from app.airvault.flujo import PROCESANDO

    ventana._estados = [parte(PROCESANDO)]
    ventana._ajustar_vigilancia()
    assert ventana._vigilante.isActive()
    ventana._al_fallar("La sesion de AirVault caduco.")
    assert not ventana._vigilante.isActive()


# ── completar el batch ─────────────────────────────────────────────

class Cierre:
    def __init__(self, completado, detalle="", quitadas=()):
        self.completado = completado
        self.detalle = detalle
        self.bloqueadas: list = []
        self.paginas = 5
        self.quitadas = list(quitadas)


def test_si_el_lote_se_cerro_se_dice(ventana):
    ventana._al_indexar({
        "resultado": ResultadoFalso(), "validas": 2, "total": 2, "lotes": 1,
        "cierres": [(TrabajoFalso(), Cierre(True))],
    })
    assert "completó en AirVault" in ventana.resumen.text()
    assert "salió de la cola de Web Index" in ventana.resumen.text()


def test_si_airvault_no_deja_cerrarlo_se_dice_por_que(ventana):
    """Es lo que hay que mirar: qué página falta completar y dónde."""
    ventana._al_indexar({
        "resultado": ResultadoFalso(), "validas": 2, "total": 3, "lotes": 1,
        "cierres": [(TrabajoFalso(), Cierre(
            False, "2 de 5 paginas no estan en verde (3, 4)"
        ))],
    })
    texto = ventana.resumen.text()
    assert "no se pudo completar" in texto
    assert "no estan en verde" in texto


def test_si_hubo_que_quitar_separadores_se_dice(ventana):
    """Es un cambio en el lote: esas páginas ya no están en AirVault."""
    ventana._al_indexar({
        "resultado": ResultadoFalso(), "validas": 2, "total": 2, "lotes": 1,
        "cierres": [(TrabajoFalso(), Cierre(True, quitadas=[1, 4, 6]))],
    })
    texto = ventana.resumen.text()
    assert "completó en AirVault" in texto
    assert "3 páginas separadoras" in texto


def test_sin_completar_marcado_no_se_dice_nada_de_cerrar(ventana):
    ventana._al_indexar({
        "resultado": ResultadoFalso(), "validas": 2, "total": 3, "lotes": 1,
        "cierres": [],
    })
    assert "cerr" not in ventana.resumen.text()


def test_el_fallo_se_cuenta_donde_se_lee(ventana):
    ventana._al_fallar("La sesion de AirVault caduco.")
    assert "caduco" in ventana.resumen.text()


def test_el_avance_sale_por_la_barra_de_la_ventana(ventana):
    """La ventana dibuja su propio avance: ya no cuelga de la principal."""
    ventana._mostrar_paso("Escribiendo en AirVault", 30, 120)
    assert ventana.estado_label.text() == "Escribiendo en AirVault"
    assert ventana.progreso.maximum() == 120
    assert ventana.progreso.value() == 30


# ── mientras escribe ───────────────────────────────────────────────

def test_con_un_lote_a_medias_no_se_cambia_de_ejecucion(ventana):
    ventana._habilitar(False)
    assert not ventana.historial.isEnabled()
    assert not ventana.boton_buscar.isEnabled()
    assert not ventana.boton_subir.isEnabled()
    assert not ventana.boton_comprobar.isEnabled()


def test_mientras_trabaja_siempre_hay_algo_que_pulsar(ventana):
    """Una espera larga no puede dejar la ventana sin salida.

    Entrar a AirVault espera hasta cinco minutos y un lote puede tardar
    quince en salir de la cola del servidor. Con todo apagado, eso era una
    ventana congelada sin nada que hacer más que matar el programa.
    """
    ventana._habilitar(False)
    assert ventana.boton_cancelar.isEnabled()
    assert ventana.boton_cerrar.isEnabled()
    ventana._habilitar(True)
    assert not ventana.boton_cancelar.isEnabled()


class HiloFalso:
    """Un hilo en marcha al que se le puede pedir que pare."""

    def __init__(self) -> None:
        self.interrumpido = False

    @staticmethod
    def isRunning() -> bool:
        return True

    def cancelar(self) -> None:
        self.interrumpido = True


def test_cerrar_con_trabajo_en_vuelo_lo_cancela_en_vez_de_negarse(ventana):
    """Antes se negaba a cerrar, y el trabajo podía estar esperando minutos.

    La ventana se quedaba sin salida: ni cerraba, ni avanzaba, ni había
    nada que pulsar. Ahora se pide la cancelación y se cierra en cuanto el
    hilo suelta los lotes que tuviera tomados, que es lo único que no se
    puede dejar a medias.
    """
    hilo = HiloFalso()
    ventana._worker = hilo
    try:
        ventana.close()
        assert hilo.interrumpido
        assert ventana._cerrar_al_terminar
        assert "Cancelando" in ventana.resumen.text()
    finally:
        ventana._worker = None
        ventana._cerrar_al_terminar = False


def test_cancelar_le_pide_al_hilo_que_pare_sin_esperarlo(ventana):
    """Esperar al hilo aquí congelaría justo lo que se quiere destrabar."""
    hilo = HiloFalso()
    ventana._worker = hilo
    try:
        ventana._cancelar()
        assert hilo.interrumpido
        assert not ventana.boton_cancelar.isEnabled()
        assert "Cancelando" in ventana.estado_label.text()
    finally:
        ventana._worker = None


def test_una_etapa_sin_cuenta_deja_la_barra_en_marcha(ventana):
    """Parada en cero se lee como que no está pasando nada."""
    ventana._mostrar_paso("Entrando a AirVault", 0, 0)
    assert ventana.progreso.maximum() == 0
    assert ventana.progreso.minimum() == 0


def test_la_bitacora_cuenta_los_pasos_y_no_repite_el_mismo(ventana):
    """Los avisos de una subida llegan por trozo; la bitácora cuenta etapas."""
    ventana._mostrar_paso("Subiendo entrega.pdf", 1, 40)
    ventana._mostrar_paso("Subiendo entrega.pdf", 2, 40)
    ventana._mostrar_paso("Buscando el lote", 0, 0)
    assert ventana.bitacora.count() == 2
    assert "Subiendo entrega.pdf" in ventana.bitacora.item(0).text()
    assert "Buscando el lote" in ventana.bitacora.item(1).text()


def test_cancelado_se_cuenta_y_no_deja_la_barra_girando(ventana):
    ventana._al_cancelar()
    assert "canceló" in ventana.resumen.text()
    assert "desbloqueados" in ventana.resumen.text()
    assert ventana.progreso.maximum() == 100


# ── integración con la ventana principal ───────────────────────────

def test_el_boton_va_en_la_fila_de_opciones_avanzadas(app):
    """Donde estaba la flecha del panel, que ya no existe."""
    from app.gui.main_window import MainWindow

    principal = MainWindow()
    try:
        fila = principal._desplegables_row
        widgets = [fila.itemAt(i).widget() for i in range(fila.count())]
        assert principal.advanced_btn in widgets
        assert principal.btn_airvault in widgets
        assert principal._airvault_window is None
    finally:
        principal.close()


def test_el_cierre_espera_al_lote_a_medio_escribir(app):
    """Destruir el hilo con un lote a medias mata el programa."""
    from app.gui.main_window import MainWindow

    class HiloFalso:
        @staticmethod
        def isRunning() -> bool:
            return True

    principal = MainWindow()
    try:
        principal._open_airvault()
        falso = HiloFalso()
        with patch.object(
            principal._airvault_window, "hilo", return_value=falso
        ):
            assert falso in principal._running_workers()
    finally:
        principal.close()


def test_al_terminar_una_exportacion_se_apunta_a_esa_corrida(app, tmp_path):
    """Aunque la ventana no se haya abierto todavía: se guarda para cuando."""
    from app.gui.main_window import MainWindow

    principal = MainWindow()
    try:
        destino = tmp_path / "BITS 18 AUG 2026 05 42"
        (destino / "datos").mkdir(parents=True)
        principal._outputs_context = "export"
        principal._on_outputs_written(destino)
        assert str(principal._airvault_corrida).endswith(
            "BITS 18 AUG 2026 05 42.CSV"
        )
        principal._open_airvault()
        assert principal._airvault_window.corrida_edit.text().endswith(
            "BITS 18 AUG 2026 05 42.CSV"
        )
    finally:
        principal.close()


# ── el procesamiento ya no exporta ─────────────────────────────────

def test_procesar_guarda_los_datos_pero_no_arma_los_pdfs(app):
    """Los PDF son la entrega, y se arman al exportar, no al procesar.

    Componerlos vuelve a abrir cada original y tarda; imponerlo al terminar
    el OCR obligaba a esperarlo a quien todavía iba a cambiar la separación
    y a exportar otra vez.
    """
    from app.models.schemas import ValidationReport
    from app.gui.main_window import MainWindow

    principal = MainWindow()
    try:
        reportes = [ValidationReport(pdf_path="a.pdf", template_name="x")]
        with patch.object(MainWindow, "_start_outputs") as salidas:
            principal._on_succeeded(reportes)
        salidas.assert_called_once()
        assert salidas.call_args.kwargs["skip_pdfs"] is True
        assert salidas.call_args.kwargs["context"] == "proceso"
    finally:
        principal.close()


def test_exportar_si_arma_los_pdfs(app):
    from app.models.schemas import ValidationReport
    from app.gui.main_window import MainWindow

    principal = MainWindow()
    try:
        principal._reports = [ValidationReport(pdf_path="a.pdf",
                                               template_name="x")]
        with patch.object(MainWindow, "_start_outputs") as salidas:
            principal._exportar()
        salidas.assert_called_once()
        assert salidas.call_args.kwargs.get("skip_pdfs", False) is False
        assert salidas.call_args.kwargs["context"] == "export"
    finally:
        principal.close()


# ── el hilo se puede parar ─────────────────────────────────────────

def test_cancelar_corta_dentro_de_una_subida_de_mil_trozos(app):
    """La cancelación entra por donde el trabajo cuenta lo que va haciendo.

    Así se puede parar dentro de una subida por trozos o de una espera de
    quince minutos sin que el recorrido del indexado sepa que existe un
    botón de cancelar.
    """
    from app.gui.airvault_window import TrabajoAirVaultWorker, TrabajoCancelado

    worker = TrabajoAirVaultWorker("comprobar", {})
    worker._avisar("Subiendo entrega.pdf", 1, 40)
    worker.cancelar()
    with pytest.raises(TrabajoCancelado):
        worker._avisar("Subiendo entrega.pdf", 2, 40)


def test_cancelar_corta_la_espera_del_lote(app):
    """Dormir de una vez dejaba el botón sin efecto hasta el otro sondeo."""
    from app.gui.airvault_window import TrabajoAirVaultWorker, TrabajoCancelado

    worker = TrabajoAirVaultWorker("comprobar", {})
    worker.cancelar()
    with pytest.raises(TrabajoCancelado):
        worker._dormir(600.0)


def test_una_cancelacion_no_se_confunde_con_el_fallo_de_una_pagina(app):
    """Por eso no es una ``Exception``.

    El indexado atrapa ``Exception`` alrededor de cada escritura para anotar
    la página que falló y seguir con las demás; una cancelación ahí quedaría
    anotada como si una bitácora estuviera rota.
    """
    from app.gui.airvault_window import TrabajoCancelado

    assert not issubclass(TrabajoCancelado, Exception)

"""El botón «Automático» y los pasos que encadena.

Comprueba lo que promete: que los pasos se elijan una sola vez y sobrevivan
al cierre, que los de AirVault se marquen y desmarquen juntos porque van uno
detrás de otro, que «Completar batch» valga lo mismo en las dos ventanas, y
que la cadena avance sola de un paso al siguiente y se corte cuando algo
sale mal.

Nada de esto procesa un PDF ni toca la red: se sustituye la escritura de
salidas por una lista y se mira por dónde pasó la cadena.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.gui.airvault_window import AirVaultWindow
from app.gui.automatizacion import (
    COMPLETAR,
    CORTADO,
    DEPURAR,
    EN_CURSO,
    ESPERAR,
    EXPORTAR,
    HECHO,
    INDEXAR,
    OMITIDO,
    PENDIENTE,
    PREPROCESAR,
    PROCESAR,
    RECORRIDO,
    SUBIR,
    CadenaAutomatica,
    MenuAutomatizacion,
    OpcionesAutomatizacion,
)
from app.gui.main_window import MainWindow
from app.models.schemas import FieldResult, PageResult, ValidationReport

RAIZ = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def pagina(numero: int, log: str | None = None, blank: bool = False) -> PageResult:
    campos = []
    if log is not None:
        campos.append(
            FieldResult(
                page_number=numero,
                field_id="log_number",
                field_type="ocr",
                value=log,
            )
        )
    return PageResult(page_number=numero, blank=blank, fields=campos)


def corrida() -> list[ValidationReport]:
    """Una bitácora repetida y una página en blanco, más dos buenas."""
    return [
        ValidationReport(
            pdf_path="primero.pdf",
            template_name="fixture",
            pages=[
                pagina(1, "2147300"),
                pagina(2, blank=True),
                pagina(3, "2147301"),
            ],
        ),
        ValidationReport(
            pdf_path="segundo.pdf",
            template_name="fixture",
            pages=[pagina(7, "2147300"), pagina(8, "2147302")],
        ),
    ]


# ── las opciones ───────────────────────────────────────────────────


def test_sin_nada_guardado_la_cadena_llega_hasta_indexar(tmp_path):
    """Los valores con los que la ventana de AirVault ya trabajaba."""
    opciones = OpcionesAutomatizacion(tmp_path)

    assert not opciones.depurar
    assert opciones.subir
    assert opciones.esperar
    assert opciones.indexar
    # Cerrar el batch en AirVault nunca se ha impuesto solo.
    assert not opciones.completar
    # Y no se escribe un archivo por el solo hecho de mirar las opciones.
    assert not (tmp_path / "airvault.json").exists()


def test_marcar_un_paso_enciende_los_que_van_antes(tmp_path):
    """No se indexa lo que no está arriba: la cadena se marca junta."""
    opciones = OpcionesAutomatizacion(tmp_path)
    opciones.fijar(SUBIR, False)
    assert not any(
        (opciones.subir, opciones.esperar, opciones.indexar, opciones.completar)
    )

    opciones.fijar(COMPLETAR, True)

    assert opciones.subir
    assert opciones.esperar
    assert opciones.indexar
    assert opciones.completar


def test_apagar_un_paso_apaga_los_que_van_despues(tmp_path):
    """Sin espera no hay batch entero que indexar ni que cerrar."""
    opciones = OpcionesAutomatizacion(tmp_path)
    opciones.fijar(COMPLETAR, True)

    opciones.fijar(ESPERAR, False)

    assert opciones.subir
    assert not opciones.esperar
    assert not opciones.indexar
    assert not opciones.completar


def test_depurar_va_suelto_y_no_arrastra_a_nadie(tmp_path):
    """Es opcional de verdad: se marca sin tocar el resto de la cadena."""
    opciones = OpcionesAutomatizacion(tmp_path)

    opciones.fijar(DEPURAR, True)

    assert opciones.depurar
    assert opciones.subir and opciones.esperar and opciones.indexar

    opciones.fijar(DEPURAR, False)

    assert not opciones.depurar
    assert opciones.subir and opciones.esperar and opciones.indexar


def test_los_pasos_elegidos_sobreviven_al_cierre(tmp_path):
    """Cerrar el programa no devuelve las opciones a lo que traían."""
    primera = OpcionesAutomatizacion(tmp_path)
    primera.fijar(DEPURAR, True)
    primera.fijar(INDEXAR, False)

    segunda = OpcionesAutomatizacion(tmp_path)

    assert segunda.depurar
    assert segunda.subir
    assert segunda.esperar
    assert not segunda.indexar
    guardado = json.loads((tmp_path / "airvault.json").read_text(encoding="utf-8"))
    assert guardado["auto_depurar"] is True
    assert guardado["auto_indexar"] is False


def test_el_menu_enseña_los_tres_pasos_que_no_se_eligen(app, tmp_path):
    """Preprocesar, procesar y exportar siempre se hacen, y la lista lo dice."""
    menu = MenuAutomatizacion(OpcionesAutomatizacion(tmp_path))
    try:
        fijos = [
            accion for accion in menu.actions()
            if accion.isCheckable() and not accion.isEnabled()
        ]
        assert [accion.text() for accion in fijos] == [
            "Preprocesar (enderezar y alinear)",
            "Procesar (OCR)",
            "Exportar CSV, JSON y PDF",
        ]
        assert all(accion.isChecked() for accion in fijos)
    finally:
        menu.deleteLater()


def test_el_menu_se_abre_encima_y_no_ocupa_sitio_en_la_ventana(app, tmp_path):
    """Es lo que arregla la pantalla baja: no entra en el reparto."""
    ventana = MainWindow()
    try:
        assert isinstance(ventana.menu_automatizacion, MenuAutomatizacion)
        assert not ventana.menu_automatizacion.isVisible()
        # No cuelga de ningún layout: un panel empotrado sí lo haría.
        assert ventana.menu_automatizacion.parentWidget() is ventana
        assert not ventana.btn_automatizacion.isCheckable()

        ventana.menu_automatizacion.abrir_sobre(ventana.btn_automatizacion)
        app.processEvents()

        assert ventana.menu_automatizacion.isVisible()
        ventana.menu_automatizacion.close()
    finally:
        ventana.close()
        app.processEvents()


def test_marcar_un_paso_no_cierra_el_menu(app, tmp_path):
    """Son varias casillas: cerrarlo en la primera obliga a reabrirlo."""
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    opciones = OpcionesAutomatizacion(tmp_path)
    menu = MenuAutomatizacion(opciones)
    try:
        menu.popup(QPoint(0, 0))
        app.processEvents()
        menu.setActiveAction(menu.accion(DEPURAR))
        menu.mouseReleaseEvent(
            QMouseEvent(
                QMouseEvent.Type.MouseButtonRelease,
                QPointF(5, 5),
                QPointF(5, 5),
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            )
        )
        app.processEvents()

        assert opciones.depurar
        assert menu.isVisible()
    finally:
        menu.close()
        menu.deleteLater()
        app.processEvents()


def test_el_menu_y_las_opciones_se_mueven_juntos(app, tmp_path):
    """Da igual por dónde se toque: es un solo valor."""
    opciones = OpcionesAutomatizacion(tmp_path)
    menu = MenuAutomatizacion(opciones)
    try:
        menu.accion(COMPLETAR).setChecked(True)
        assert opciones.completar

        opciones.fijar(SUBIR, False)

        assert not menu.accion(SUBIR).isChecked()
        assert not menu.accion(COMPLETAR).isChecked()
    finally:
        menu.deleteLater()


def test_las_dos_ventanas_enseñan_el_mismo_menu_de_pasos(app, tmp_path):
    """El de AirVault y el de la principal son la misma elección."""
    opciones = OpcionesAutomatizacion(tmp_path)
    principal = MenuAutomatizacion(opciones)
    ventana = AirVaultWindow(RAIZ, opciones)
    try:
        principal.accion(COMPLETAR).setChecked(True)

        assert ventana.menu_automatizacion.accion(COMPLETAR).isChecked()
        # Y «Completar batch» sigue siendo además una casilla a la vista.
        assert ventana.completar_check.isChecked()

        ventana.completar_check.setChecked(False)

        assert not principal.accion(COMPLETAR).isChecked()
        assert not opciones.completar
    finally:
        ventana.close()
        principal.deleteLater()
        app.processEvents()


# ── la cadena ──────────────────────────────────────────────────────


@pytest.fixture
def ventana(app, tmp_path):
    """La ventana principal con sus opciones aparte y sin escribir nada."""
    window = MainWindow()
    window._automatizacion = OpcionesAutomatizacion(tmp_path)
    # La línea de pasos se construyó con las opciones de verdad; aquí las
    # opciones son otras y tiene que contar sobre esas.
    window.cadena._opciones = window._automatizacion
    window.cadena.reiniciar()
    window._reports = corrida()
    window._corrida_dir = RAIZ / "output" / "BITS 19 AUG 2026 05 00"
    window.escrituras: list[tuple[int, str, bool]] = []
    window._start_outputs = lambda reports, context, skip_pdfs=False: (
        window.escrituras.append((len(reports), context, skip_pdfs))
    )
    yield window
    window.close()
    app.processEvents()


def test_el_boton_dice_lo_que_va_a_hacer(ventana):
    assert ventana.btn_automatico.text() == "Automático"
    assert ventana._pasos_automaticos() == (
        "preprocesar > procesar > exportar > subir > esperar > indexar"
    )

    ventana._automatizacion.fijar(DEPURAR, True)
    ventana._automatizacion.fijar(COMPLETAR, True)

    assert ventana._pasos_automaticos() == (
        "preprocesar > procesar > depurar > exportar > subir > esperar > "
        "indexar > completar"
    )


def test_sin_depurar_el_proceso_pasa_derecho_a_exportar(ventana):
    ventana._auto_en_marcha = True

    ventana._seguir_automatico("proceso")

    assert ventana.escrituras == [(2, "export", False)]


def test_con_depurar_se_quitan_repetidas_y_blancas_sin_abrir_el_cuadro(ventana):
    """Y solo después se exporta, para que los PDF salgan ya sin ellas."""
    ventana._automatizacion.fijar(DEPURAR, True)
    ventana._auto_en_marcha = True

    ventana._seguir_automatico("proceso")

    assert ventana.escrituras == [(2, "depurar", True)]
    # De la bitácora repetida se va la segunda aparición, no la primera.
    assert [
        (report.pdf_path, [p.page_number for p in report.pages])
        for report in ventana._reports
    ] == [("primero.pdf", [1, 3]), ("segundo.pdf", [8])]

    ventana._seguir_automatico("depurar")

    assert ventana.escrituras[-1] == (2, "export", False)


def test_una_corrida_limpia_no_se_queda_esperando_a_la_depuracion(ventana):
    """Sin nada que quitar, la cadena sigue a exportar en el mismo paso."""
    ventana._automatizacion.fijar(DEPURAR, True)
    ventana._reports = [
        ValidationReport(
            pdf_path="unico.pdf",
            template_name="fixture",
            pages=[pagina(1, "2147300"), pagina(2, "2147301")],
        )
    ]
    ventana._auto_en_marcha = True

    ventana._seguir_automatico("proceso")

    assert ventana.escrituras == [(1, "export", False)]


def test_al_exportar_la_cadena_pasa_a_airvault_y_suelta_esta_ventana(ventana):
    pedidas = []
    ventana._subir_automatico = lambda: pedidas.append(True)
    ventana._auto_en_marcha = True

    ventana._seguir_automatico("export")

    assert pedidas == [True]
    assert not ventana._auto_en_marcha


def test_sin_subir_marcado_la_cadena_termina_al_exportar(ventana):
    pedidas = []
    ventana._subir_automatico = lambda: pedidas.append(True)
    ventana._automatizacion.fijar(SUBIR, False)
    ventana._auto_en_marcha = True

    ventana._seguir_automatico("export")

    assert pedidas == []
    assert not ventana._auto_en_marcha


def test_cancelar_corta_la_cadena_y_no_exporta_lo_cancelado(ventana):
    """Cancelar es cancelar la entrega, no solo el paso que estaba corriendo."""
    ventana._auto_en_marcha = True
    ventana._last_run_cancelled = True

    ventana._seguir_automatico("proceso")

    assert ventana.escrituras == []
    assert not ventana._auto_en_marcha


def test_un_fallo_al_generar_salidas_deja_la_cadena_parada(ventana):
    ventana._auto_en_marcha = True

    ventana._on_outputs_failed("no se pudo escribir el CSV")

    assert not ventana._auto_en_marcha


# ── hasta dónde llegó ──────────────────────────────────────────────
#
# El menú dice hasta dónde se va a llegar; la línea de pasos, hasta dónde
# se llegó. Hacía falta porque la ventana principal suelta la cadena al
# exportar y lo que sigue pasa en la de AirVault: desde aquí no había forma
# de saber si la entrega terminó de subirse o se quedó a medias.

def test_los_pasos_que_no_se_eligieron_no_cuentan(app, tmp_path):
    opciones = OpcionesAutomatizacion(tmp_path)
    opciones.fijar(COMPLETAR, False)
    cadena = CadenaAutomatica(opciones)

    assert cadena.estado(DEPURAR) == OMITIDO
    assert cadena.estado(COMPLETAR) == OMITIDO
    assert cadena.estado(PROCESAR) == PENDIENTE
    # Preprocesar, procesar, exportar, subir, esperar e indexar.
    assert cadena.resumen() == "Automático: 0 de 6 pasos"


def test_cambiar_los_pasos_rehace_la_cuenta(app, tmp_path):
    opciones = OpcionesAutomatizacion(tmp_path)
    cadena = CadenaAutomatica(opciones)

    opciones.fijar(COMPLETAR, True)

    assert cadena.estado(COMPLETAR) == PENDIENTE
    assert cadena.resumen() == "Automático: 0 de 7 pasos"


def test_empezar_un_paso_da_por_hecho_el_anterior(app, tmp_path):
    """Los de AirVault llegan de otra ventana y alguno puede perderse.

    La cadena avanza en un solo sentido y no hay dos pasos a la vez, así
    que enterarse del cuarto es enterarse de que el tercero acabó.
    """
    cadena = CadenaAutomatica(OpcionesAutomatizacion(tmp_path))

    cadena.marcar(PREPROCESAR, EN_CURSO)
    cadena.marcar(PROCESAR, EN_CURSO)
    cadena.marcar(SUBIR, EN_CURSO)

    assert cadena.estado(PREPROCESAR) == HECHO
    assert cadena.estado(PROCESAR) == HECHO
    assert cadena.resumen() == "Automático: subir (2 de 6 pasos)"


def test_la_cadena_completa_lo_dice_sin_contar(app, tmp_path):
    """Sin subir, la entrega termina en la exportación y ya está entera."""
    opciones = OpcionesAutomatizacion(tmp_path)
    opciones.fijar(SUBIR, False)
    cadena = CadenaAutomatica(opciones)

    for paso in (PREPROCESAR, PROCESAR, EXPORTAR):
        cadena.marcar(paso, HECHO)

    assert cadena.resumen() == "Automático: completo"


def test_cortar_deja_escrito_donde_se_detuvo(app, tmp_path):
    cadena = CadenaAutomatica(OpcionesAutomatizacion(tmp_path))
    cadena.marcar(PREPROCESAR, HECHO)
    cadena.marcar(PROCESAR, HECHO)
    cadena.marcar(EXPORTAR, EN_CURSO)

    cadena.cortar()

    assert cadena.estado(EXPORTAR) == CORTADO
    assert cadena.resumen() == (
        "Automático: se cortó en «Exportar» (2 de 6 pasos)"
    )


def test_el_recorrido_de_la_ventana_va_marcando_la_linea(ventana):
    ventana._auto_en_marcha = True
    ventana.cadena.marcar(PROCESAR, EN_CURSO)

    ventana._seguir_automatico("proceso")

    assert ventana.cadena.estado(PROCESAR) == HECHO
    assert ventana.cadena.estado(EXPORTAR) == EN_CURSO


def test_cancelar_deja_el_paso_en_curso_marcado_como_cortado(ventana):
    ventana._auto_en_marcha = True
    ventana.cadena.marcar(PROCESAR, EN_CURSO)

    ventana._cortar_automatico("se canceló el procesamiento")

    assert ventana.cadena.estado(PROCESAR) == CORTADO
    assert "se cortó en «Procesar»" in ventana.cadena.resumen()


# ── el primer paso: preprocesar ────────────────────────────────────
#
# El pipeline recorre el batch entero enderezando y alineando cada página
# antes de leer ninguna. Ese tramo se contaba como si ya estuviera
# procesando, así que el primer paso parecía atascado mientras duraba.

def test_la_linea_empieza_por_preprocesar(app, tmp_path):
    cadena = CadenaAutomatica(OpcionesAutomatizacion(tmp_path))

    assert RECORRIDO[0] == PREPROCESAR
    assert RECORRIDO[1] == PROCESAR
    assert cadena.elegido(PREPROCESAR)
    assert cadena.estado(PREPROCESAR) == PENDIENTE


def test_la_calibracion_es_el_paso_de_preprocesar(ventana):
    """Mientras se calibra manda «Preprocesar»; con la primera página, no."""
    ventana._auto_en_marcha = True
    ventana.cadena.marcar(PREPROCESAR, EN_CURSO)

    # La calibración avisa como etapa, sin páginas leídas.
    ventana._on_progress(0, 40, "Calibrando alineación (página 3/40)")

    assert ventana.cadena.estado(PREPROCESAR) == EN_CURSO
    assert ventana.cadena.estado(PROCESAR) == PENDIENTE

    ventana._on_progress(1, 40, "Procesando página 1/40")

    assert ventana.cadena.estado(PREPROCESAR) == HECHO
    assert ventana.cadena.estado(PROCESAR) == EN_CURSO


def test_un_batch_sin_calibracion_no_deja_el_primer_paso_colgado(ventana):
    """Sin alineación no hay tramo que calibrar: el paso queda hecho igual."""
    ventana._auto_en_marcha = True
    ventana.cadena.marcar(PREPROCESAR, EN_CURSO)

    ventana._seguir_automatico("proceso")

    assert ventana.cadena.estado(PREPROCESAR) == HECHO
    assert ventana.cadena.estado(PROCESAR) == HECHO

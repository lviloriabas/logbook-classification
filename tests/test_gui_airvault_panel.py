"""La sección «Indexar en AirVault» de la ventana principal.

Comprueba lo que la ventana promete: que cerrada no cueste sitio, que no
se pueda indexar sin haber revisado antes, que el avance salga por la barra
que ya existe y que cerrar el programa espere al lote a medio escribir.

Nada de esto toca la red: la sección solo prepara el trabajo y arranca el
hilo, y lo que decide si una página se escribe vive en ``app.airvault``.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLineEdit

from app.gui.airvault_panel import AirVaultPanel

RAIZ = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def panel(app):
    return AirVaultPanel(RAIZ)


def corrida(tmp_path, nombre="BITS 18 AUG 2026 05 42", con_pdf=True):
    carpeta = tmp_path / "output" / nombre
    (carpeta / "datos").mkdir(parents=True)
    csv = carpeta / "datos" / f"{nombre}.CSV"
    csv.write_text("file,page,log_number,matricula,date\n", encoding="utf-8")
    if con_pdf:
        (carpeta / f"{nombre}.pdf").write_bytes(b"%PDF-1.4\n")
    return csv


# ── forma del panel ────────────────────────────────────────────────

def test_arranca_cerrado(panel):
    assert panel.isHidden()
    assert panel.boton_desplegar.text() == "Indexar en AirVault"


def test_la_flecha_lo_abre_y_lo_cierra(panel):
    panel.boton_desplegar.setChecked(True)
    assert not panel.isHidden()
    panel.boton_desplegar.setChecked(False)
    assert panel.isHidden()


def test_la_cookie_no_se_ve_al_teclearla(panel):
    """Vale tanto como la contrasena: no puede quedar a la vista."""
    assert panel.cookie_edit.echoMode() == QLineEdit.EchoMode.Password


def test_indexar_esta_apagado_hasta_que_haya_revision(panel):
    assert not panel.boton_indexar.isEnabled()
    assert not panel.boton_reporte.isEnabled()


# ── la corrida ─────────────────────────────────────────────────────

def test_apunta_a_la_corrida_y_propone_el_nombre_del_lote(panel, tmp_path):
    csv = corrida(tmp_path)
    panel.fijar_corrida(csv)
    assert panel.corrida_edit.text() == str(csv)
    assert panel.lote_edit.text() == "DP | BITS 18 AUG 2026 05 42"


def test_el_campo_de_la_corrida_no_se_teclea(panel):
    """Se elige con «Buscar…» o lo pone la corrida que acaba de terminar."""
    assert panel.corrida_edit.isReadOnly()


def test_cambiar_de_corrida_invalida_la_revision_anterior(panel, tmp_path):
    panel._revision = {"algo": 1}
    panel.boton_indexar.setEnabled(True)
    panel.fijar_corrida(corrida(tmp_path))
    assert panel._revision == {}
    assert not panel.boton_indexar.isEnabled()


# ── no se arranca sin lo imprescindible ────────────────────────────

def test_sin_corrida_no_arranca_nada(panel):
    panel._revisar()
    assert panel._worker is None
    assert "Falta elegir la corrida" in panel.resumen.text()


def test_sin_nombre_de_lote_no_arranca_nada(panel, tmp_path):
    panel.fijar_corrida(corrida(tmp_path))
    panel.lote_edit.setText("   ")
    panel._revisar()
    assert panel._worker is None
    assert "Falta el nombre del lote" in panel.resumen.text()


def test_indexar_sin_revision_no_hace_nada(panel):
    panel._indexar()
    assert panel._worker is None


# ── lo que cuenta al terminar ──────────────────────────────────────

class PlanFalso:
    batch_id = "003SRO"

    def resumen(self):
        return {"total": 5, "escribibles": 2, "bloqueadas": 1,
                "separadores": 2, "avisos_globales": 0}


def test_la_revision_dice_que_no_se_ha_escrito_nada(panel, tmp_path):
    panel._al_revisar({
        "partes": [("DP | BIT 003SRO", PlanFalso())],
        "reporte": tmp_path / "r.html",
    })
    texto = panel.resumen.text()
    assert "003SRO" in texto and "5 páginas" in texto
    assert "2 se escribirían" in texto
    assert "Nada se ha escrito todavía" in texto
    assert panel.boton_indexar.isEnabled()
    assert panel.boton_reporte.isEnabled()


class ResultadoFalso:
    escritas, omitidas, fallidas = 2, 1, 0
    detalles: list = []
    interrumpido = ""


def test_al_indexar_cuenta_como_quedo_el_lote(panel):
    panel._al_indexar({"resultado": ResultadoFalso(), "validas": 2, "total": 3})
    texto = panel.resumen.text()
    assert "Escritas 2" in texto and "2 de 3 páginas válidas" in texto


def test_un_indexado_cortado_dice_que_lo_que_falta_se_retoma(panel):
    """Con la sesion o la red caidas, lo escrito no se pierde ni se repite."""
    class Cortado(ResultadoFalso):
        interrumpido = "La sesion de AirVault caduco."

    panel._al_indexar({"resultado": Cortado(), "validas": 2, "total": 5})
    texto = panel.resumen.text()
    assert "se cortó" in texto and "caduco" in texto
    assert "sin repetir lo escrito" in texto


def test_el_fallo_se_cuenta_donde_se_lee(panel):
    panel._al_fallar("La sesion de AirVault caduco.")
    assert "caduco" in panel.resumen.text()


def test_el_avance_sale_por_las_senales(panel):
    estados, progresos = [], []
    panel.estado_cambiado.connect(estados.append)
    panel.progreso_cambiado.connect(lambda h, t: progresos.append((h, t)))
    panel._mostrar_paso("Escribiendo en AirVault", 30, 120)
    assert estados == ["Escribiendo en AirVault"]
    assert progresos == [(30, 120)]


# ── integración con la ventana ─────────────────────────────────────

def test_la_flecha_va_en_la_fila_de_opciones_avanzadas(app):
    """Apilada, le costaba 15 px de alto y la ventana no cabia en 1024x768."""
    from app.gui.main_window import MainWindow

    ventana = MainWindow()
    try:
        fila = ventana._desplegables_row
        widgets = [
            fila.itemAt(i).widget() for i in range(fila.count())
        ]
        assert ventana.advanced_btn in widgets
        assert ventana.airvault_panel.boton_desplegar in widgets
    finally:
        ventana.close()


def test_el_cierre_espera_al_lote_a_medio_escribir(app):
    """Destruir el hilo con un lote a medias mata el programa."""
    from app.gui.main_window import MainWindow

    class HiloFalso:
        @staticmethod
        def isRunning() -> bool:
            return True

    ventana = MainWindow()
    try:
        falso = HiloFalso()
        with patch.object(
            ventana.airvault_panel, "hilo", return_value=falso
        ):
            assert falso in ventana._running_workers()
    finally:
        ventana.close()


def test_al_terminar_una_exportacion_la_seccion_apunta_a_esa_corrida(
    app, tmp_path
):
    from app.gui.main_window import MainWindow

    ventana = MainWindow()
    try:
        destino = tmp_path / "BITS 18 AUG 2026 05 42"
        (destino / "datos").mkdir(parents=True)
        ventana._outputs_context = "export"
        ventana._on_outputs_written(destino)
        assert ventana.airvault_panel.corrida_edit.text().endswith(
            "BITS 18 AUG 2026 05 42.CSV"
        )
    finally:
        ventana.close()


# ── el procesamiento ya no exporta ─────────────────────────────────

def test_procesar_guarda_los_datos_pero_no_arma_los_pdfs(app):
    """Los PDF son la entrega, y se arman al exportar, no al procesar.

    Componerlos vuelve a abrir cada original y tarda; imponerlo al terminar
    el OCR obligaba a esperarlo a quien todavía iba a cambiar la separación
    y a exportar otra vez.
    """
    from app.models.schemas import ValidationReport
    from app.gui.main_window import MainWindow

    ventana = MainWindow()
    try:
        reportes = [ValidationReport(pdf_path="a.pdf", template_name="x")]
        with patch.object(MainWindow, "_start_outputs") as salidas:
            ventana._on_succeeded(reportes)
        salidas.assert_called_once()
        assert salidas.call_args.kwargs["skip_pdfs"] is True
        assert salidas.call_args.kwargs["context"] == "proceso"
    finally:
        ventana.close()


def test_exportar_si_arma_los_pdfs(app):
    from app.models.schemas import ValidationReport
    from app.gui.main_window import MainWindow

    ventana = MainWindow()
    try:
        ventana._reports = [ValidationReport(pdf_path="a.pdf",
                                             template_name="x")]
        with patch.object(MainWindow, "_start_outputs") as salidas:
            ventana._exportar()
        salidas.assert_called_once()
        assert salidas.call_args.kwargs.get("skip_pdfs", False) is False
        assert salidas.call_args.kwargs["context"] == "export"
    finally:
        ventana.close()

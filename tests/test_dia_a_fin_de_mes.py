"""La ejecución a fin de mes no lee el día, y la ventana lo respeta.

Cuando la fecha del CSV se representa con el último día del mes, el día que
lee el OCR no llega a ninguna salida: son tres recortes por página que nadie
mira. La ejecución deja de leerlo y lo deja anotado, porque a partir de ahí
esa ejecución ya no puede volver a representarse con el día exacto.

Al indexar, la ventana de AirVault ofrece la misma elección sobre lo que ya
está exportado: una ejecución con día exacto todavía puede escribirse a fin
de mes, y una que fue a fin de mes no puede escribirse con el día, así que
esa opción se apaga en vez de ofrecer algo que no se puede dar.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.core.config import AppConfig
from app.core.pipeline import DAY_FIELD_IDS, DAY_NOT_READ_NOTE
from app.gui.csv_utils import run_read_day
from app.models.schemas import FieldResult, PageResult, Status
from app.reports.csv_reporter import CSV_DATE_MONTH_END, CSV_DATE_SPECIFIC


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


# ── la ejecución ───────────────────────────────────────────────────


def test_a_fin_de_mes_la_configuracion_apaga_la_lectura_del_dia():
    assert AppConfig().read_day is True
    assert AppConfig(read_day=False).read_day is False


def test_las_tres_casillas_del_dia_son_las_que_se_saltan():
    # La casilla grande y sus dos celdas de carácter. El mes y el año se
    # siguen leyendo: son los que deciden la fecha que se escribe.
    assert DAY_FIELD_IDS == {"day", "day_1", "day_2"}


def test_la_casilla_vacia_dice_que_nadie_la_miro():
    """No es lo mismo que no se pudo leer, y el CSV no debe decir lo mismo."""
    from app.core.pipeline import _mark_day_not_read

    pagina = PageResult(page_number=1, fields=[
        FieldResult(page_number=1, field_id="day", field_type="ocr",
                    value=None, status=Status.ERROR,
                    comment="Required field empty"),
        FieldResult(page_number=1, field_id="month", field_type="ocr",
                    value="AGO", status=Status.OK),
    ])

    _mark_day_not_read(pagina)

    dia = next(f for f in pagina.fields if f.field_id == "day")
    assert dia.status is Status.WARNING
    assert dia.comment == DAY_NOT_READ_NOTE
    assert dia.source == "csv_date_policy"
    assert dia.inference_method == "month_end_policy"
    # El mes no se toca.
    mes = next(f for f in pagina.fields if f.field_id == "month")
    assert mes.value == "AGO" and mes.status is Status.OK


def test_el_corrector_completa_el_dia_sin_llamarlo_ilegible():
    """El día se rellena igual, pero el motivo que se anota es otro."""
    from app.validation.date_corrector import correct_dates_by_book

    paginas = []
    for indice, log in enumerate(("2287320", "2287321"), start=1):
        pagina = PageResult(page_number=indice, fields=[
            FieldResult(page_number=indice, field_id="log_number",
                        field_type="ocr", value=log, confidence=0.9),
            FieldResult(page_number=indice, field_id="day", field_type="ocr",
                        value=None, status=Status.WARNING,
                        comment=DAY_NOT_READ_NOTE,
                        source="csv_date_policy",
                        inference_method="month_end_policy"),
            FieldResult(page_number=indice, field_id="month",
                        field_type="ocr", value="AGO", confidence=0.9),
            FieldResult(page_number=indice, field_id="year",
                        field_type="ocr", value="26", confidence=0.9),
        ])
        paginas.append(pagina)
    from app.models.schemas import ValidationReport
    reporte = ValidationReport(
        pdf_path="a.pdf", template_name="t", pages=paginas
    )

    correct_dates_by_book([reporte])

    for pagina in paginas:
        dia = next(f for f in pagina.fields if f.field_id == "day")
        assert dia.value == "31"
        assert dia.source == "csv_date_policy"
        assert "fin de mes" in dia.comment
        assert "not read" not in dia.comment.lower()
        assert pagina.date == "2026/08/31"


# ── lo que queda escrito de la ejecución ───────────────────────────


def _ejecucion(tmp_path: Path, dia_leido) -> Path:
    datos = tmp_path / "datos"
    datos.mkdir(parents=True, exist_ok=True)
    csv_path = datos / "corrida.CSV"
    csv_path.write_text("file,page\n", encoding="utf-8")
    payload = {"corrida": "corrida", "reportes": []}
    if dia_leido is not None:
        payload["dia_leido"] = dia_leido
    (datos / "corrida.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    return csv_path


def test_una_ejecucion_a_fin_de_mes_queda_marcada(tmp_path: Path):
    assert run_read_day(_ejecucion(tmp_path, False)) is False


def test_una_ejecucion_con_dia_exacto_queda_marcada(tmp_path: Path):
    assert run_read_day(_ejecucion(tmp_path, True)) is True


def test_una_ejecucion_anterior_a_la_marca_cuenta_como_leida(tmp_path: Path):
    # Todas leían el día, así que la ausencia de la clave se responde que sí.
    assert run_read_day(_ejecucion(tmp_path, None)) is True


def test_un_csv_suelto_no_apaga_la_opcion(tmp_path: Path):
    suelto = tmp_path / "otro.CSV"
    suelto.write_text("file,page\n", encoding="utf-8")

    assert run_read_day(suelto) is True


def test_el_csv_completo_hereda_la_marca_del_minimo(tmp_path: Path):
    minimo = _ejecucion(tmp_path, False)
    completo = minimo.with_name("corrida_completo.CSV")
    completo.write_text("file,page\n", encoding="utf-8")

    assert run_read_day(completo) is False


# ── la ventana de AirVault ─────────────────────────────────────────


def _ventana(app, raiz: Path):
    from app.gui.airvault_window import AirVaultWindow

    return AirVaultWindow(raiz)


def test_la_lista_ofrece_las_dos_fechas_y_abre_en_fin_de_mes(app, tmp_path):
    ventana = _ventana(app, tmp_path)
    try:
        assert [
            ventana.fecha_combo.itemText(i)
            for i in range(ventana.fecha_combo.count())
        ] == ["Fin de mes", "Día exacto"]
    finally:
        ventana.close()


def test_una_ejecucion_con_dia_deja_elegir_las_dos(app, tmp_path):
    ventana = _ventana(app, tmp_path)
    try:
        ventana._sincronizar_fecha(_ejecucion(tmp_path, True))

        assert ventana.fecha_combo.model().item(1).isEnabled()
        # Se abre en lo que la ejecución trae, no en lo que se prefiere en
        # general: lo que se ve es lo que se va a escribir.
        assert ventana.fin_de_mes() is False
    finally:
        ventana.close()


def test_una_ejecucion_a_fin_de_mes_apaga_el_dia_exacto(app, tmp_path):
    from app.gui.airvault_window import TOOLTIP_FECHA_SIN_DIA

    ventana = _ventana(app, tmp_path)
    try:
        ventana._sincronizar_fecha(_ejecucion(tmp_path, False))

        assert not ventana.fecha_combo.model().item(1).isEnabled()
        assert ventana.fin_de_mes() is True
        assert ventana.fecha_combo.toolTip() == TOOLTIP_FECHA_SIN_DIA
    finally:
        ventana.close()


def test_la_eleccion_viaja_al_hilo_con_las_demas_opciones(app, tmp_path):
    ventana = _ventana(app, tmp_path)
    try:
        ventana._sincronizar_fecha(_ejecucion(tmp_path, True))
        ventana.fecha_combo.setCurrentIndex(0)

        assert ventana.fin_de_mes() is True
    finally:
        ventana.close()

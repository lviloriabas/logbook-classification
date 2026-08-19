"""El reporte de revision es lo que se aprueba antes de escribir."""

from __future__ import annotations

import csv

from app.airvault.indexer import Indexador
from app.airvault.model import Manifiesto, Registro
from app.airvault.report import (
    COLUMNAS,
    escribir_csv,
    escribir_html,
    resumen_texto,
)
from tests.airvault_fake import ClienteFalso, pagina
from app.airvault.config import CAMPO_LOG_NUMBER


def _plan(cliente=None):
    registros = [
        Registro(seq=1, matricula="HP-1848CMP", log_number="2287325",
                 fecha="2026/08/31", fleet="NG",
                 archivo_origen="Image_001.pdf", pagina_origen=1),
        Registro(seq=2, matricula="HP-1848CMP", log_number="2287326",
                 fecha="2026/08/31", fleet="NG", fleet_inferido=True,
                 archivo_origen="Image_001.pdf", pagina_origen=2),
    ]
    manifiesto = Manifiesto(job_id="t", nombre_batch="DP | PRUEBA",
                            batch_id="003TEST", registros=registros)
    cliente = cliente or ClienteFalso(page_count=2)
    return Indexador(cliente, manifiesto, ["HP-1848CMP"]).planificar(2)


def test_csv_tiene_una_fila_por_pagina(tmp_path):
    ruta = escribir_csv(_plan(), tmp_path / "revision.csv")
    with ruta.open(encoding="utf-8-sig", newline="") as handle:
        filas = list(csv.DictReader(handle))
    assert len(filas) == 2
    assert list(filas[0].keys()) == list(COLUMNAS)


def test_csv_marca_la_flota_inferida(tmp_path):
    ruta = escribir_csv(_plan(), tmp_path / "revision.csv")
    with ruta.open(encoding="utf-8-sig", newline="") as handle:
        filas = list(csv.DictReader(handle))
    assert filas[0]["fleet_inferido"] == ""
    assert filas[1]["fleet_inferido"] == "si"


def test_csv_dice_que_accion_tomaria(tmp_path):
    cliente = ClienteFalso(
        paginas={2: pagina(2, valores={CAMPO_LOG_NUMBER: "9999999"})},
        page_count=2,
    )
    ruta = escribir_csv(_plan(cliente), tmp_path / "revision.csv")
    with ruta.open(encoding="utf-8-sig", newline="") as handle:
        filas = list(csv.DictReader(handle))
    assert filas[0]["accion"] == "escribir"
    assert filas[1]["accion"] == "bloqueada"
    assert "desalineado" in filas[1]["avisos"]


def test_html_es_autocontenido(tmp_path):
    ruta = escribir_html(_plan(), tmp_path / "revision.html", "Prueba")
    contenido = ruta.read_text(encoding="utf-8")
    assert "<table>" in contenido
    assert "src=" not in contenido and "href=" not in contenido
    assert "003TEST" in contenido


def test_html_escapa_el_contenido(tmp_path):
    plan = _plan()
    plan.paginas[0].registro.archivo_origen = "<script>x</script>"
    ruta = escribir_html(plan, tmp_path / "revision.html")
    assert "<script>x</script>" not in ruta.read_text(encoding="utf-8")


def test_generar_el_reporte_no_escribe_en_airvault(tmp_path):
    cliente = ClienteFalso(page_count=2)
    plan = _plan(cliente)
    escribir_csv(plan, tmp_path / "revision.csv")
    escribir_html(plan, tmp_path / "revision.html")
    assert cliente.escrituras == []


def test_resumen_texto_lista_los_motivos():
    cliente = ClienteFalso(
        paginas={2: pagina(2, valores={CAMPO_LOG_NUMBER: "9999999"})},
        page_count=2,
    )
    texto = resumen_texto(_plan(cliente))
    assert "desalineado: 1" in texto
    assert "se escribirian: 1" in texto

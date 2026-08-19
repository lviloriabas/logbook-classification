"""Pruebas de stats.json (estadísticas de la corrida) y del JSON
consolidado de la carpeta datos/."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.models.schemas import FieldResult, PageResult, ValidationReport
from app.reports.json_reporter import JsonReporter
from app.reports.stats import construir_stats, escribir_stats
from app.validation.discrepancias import (
    CampoAfectado,
    Categoria,
    Discrepancia,
    TipoEntrada,
)


def _page(pn: int, log, mat, date=None, blank=False) -> PageResult:
    page = PageResult(page_number=pn, date=date, blank=blank)
    if log is not None:
        page.add_field(FieldResult(page_number=pn, field_id="log_number",
                                   field_type="ocr", value=log,
                                   confidence=1.0, status="OK"))
    if mat is not None:
        page.add_field(FieldResult(page_number=pn, field_id="matricula",
                                   field_type="ocr", value=mat,
                                   confidence=1.0, status="OK"))
    return page


def _reporte(name: str, *pages: PageResult) -> ValidationReport:
    return ValidationReport(pdf_path=name, template_name="fixture",
                            pages=list(pages))


def _discrepancia(pdf: str, pagina: int, mat, categoria) -> Discrepancia:
    return Discrepancia(
        pdf_path=pdf, page_number=pagina, matricula=mat,
        log_number=2147300, tipo=TipoEntrada.VUELO, categoria=categoria,
        campos=[CampoAfectado(field_id="pilot_signature",
                              categoria=categoria,
                              razon="Falta firma de piloto")],
    )


class TestConstruirStats(unittest.TestCase):
    def setUp(self):
        self.reports = [
            _reporte(
                "a.pdf",
                _page(1, "2147300", "HP-1534CMP", date="2026/07/15"),
                _page(2, "2147301", "HP-1534CMP", date="2026/07/16"),
                _page(3, "2147302", "HP-1534CMP", date=None),
                _page(4, "2147303", None, date="2026/08/01"),
                _page(5, None, None, blank=True),
            ),
            _reporte(
                "b.pdf",
                _page(1, "2271600", "HP-1538CMP", date="2026/08/02"),
            ),
        ]

    def test_totales(self):
        stats = construir_stats(self.reports, corrida="corrida X")
        self.assertEqual(stats["corrida"], "corrida X")
        self.assertEqual(stats["total_bitacoras"], 2)
        self.assertEqual(stats["total_paginas"], 6)
        self.assertEqual(stats["paginas_en_blanco"], 1)
        self.assertEqual(stats["paginas_validas"], 5)
        self.assertEqual(
            stats["bitacoras"],
            [{"archivo": "a.pdf", "paginas": 5},
             {"archivo": "b.pdf", "paginas": 1}],
        )

    def test_por_matricula_y_mes(self):
        stats = construir_stats(self.reports)
        self.assertEqual(
            stats["por_matricula"],
            {
                "HP-1534CMP": {"total": 3,
                               "por_mes": {"2026-07": 2, "sf": 1}},
                "HP-1538CMP": {"total": 1, "por_mes": {"2026-08": 1}},
                "sin_matricula": {"total": 1, "por_mes": {"2026-08": 1}},
            },
        )
        self.assertEqual(
            stats["por_mes"],
            {"2026-07": 2, "2026-08": 2, "sf": 1},
        )

    def test_sin_determinar(self):
        stats = construir_stats(self.reports)
        self.assertEqual(stats["sin_matricula"], 1)
        self.assertEqual(stats["sin_fecha"], 1)

    def test_discrepancias(self):
        entradas = [
            _discrepancia("a.pdf", 1, "HP-1534CMP", Categoria.MISSING),
            _discrepancia("a.pdf", 3, "HP-1534CMP", Categoria.UNCERTAIN),
            _discrepancia("b.pdf", 1, None, Categoria.MISSING),
        ]
        stats = construir_stats(self.reports, entradas=entradas)
        disc = stats["discrepancias"]
        self.assertEqual(disc["total"], 3)
        self.assertEqual(disc["faltantes"], 2)
        self.assertEqual(disc["incierta"], 1)
        self.assertEqual(disc["por_matricula"],
                         {"HP-1534CMP": 2, "sin_matricula": 1})
        self.assertEqual(len(disc["detalle"]), 3)
        self.assertEqual(disc["detalle"][0]["categoria"], "missing")
        self.assertEqual(disc["detalle"][0]["razones"],
                         ["Falta firma de piloto"])

    def test_sin_discrepancias(self):
        disc = construir_stats(self.reports)["discrepancias"]
        self.assertEqual(disc["total"], 0)
        self.assertEqual(disc["detalle"], [])

    def test_separacion_completa_sin_excluidas(self):
        stats = construir_stats(self.reports, separar_por=["avion", "mes"])
        sep = stats["separacion"]
        self.assertEqual(sep["criterios"], ["avion", "mes"])
        self.assertEqual(sep["paginas_distribuidas"], 5)
        self.assertEqual(sep["paginas_excluidas_por_discrepancia"], 0)
        self.assertEqual(sep["paginas_fuera"], 0)
        self.assertTrue(sep["completa"])
        archivos = {p["archivo"]: p["paginas"] for p in sep["pdfs"]}
        self.assertEqual(
            archivos,
            {
                "HP-1534CMP_2026-JUL.pdf": 2,
                "HP-1534CMP_sf.pdf": 1,
                "HP-1538CMP_2026-AUG.pdf": 1,
                "revisar.pdf": 1,
            },
        )
        self.assertEqual(sep["total_pdfs"], 4)

    def test_separacion_con_excluidas(self):
        excluidas = {("a.pdf", 1), ("a.pdf", 3)}
        stats = construir_stats(
            self.reports, separar_por=["avion"], excluidas=excluidas
        )
        sep = stats["separacion"]
        self.assertEqual(sep["paginas_distribuidas"], 3)
        self.assertEqual(sep["paginas_excluidas_por_discrepancia"], 2)
        self.assertEqual(sep["paginas_fuera"], 0)
        self.assertTrue(sep["completa"])
        archivos = {p["archivo"]: p["paginas"] for p in sep["pdfs"]}
        self.assertEqual(
            archivos,
            {"HP-1534CMP.pdf": 1, "HP-1538CMP.pdf": 1,
             "revisar.pdf": 1},
        )

    def test_sin_separacion_no_hay_bloque(self):
        stats = construir_stats(self.reports)
        self.assertNotIn("separacion", stats)

    def test_bloque_vlm_sin_datos(self):
        stats = construir_stats(self.reports)
        self.assertFalse(stats["vlm"]["activo"])
        self.assertEqual(stats["vlm"]["crops_consultados"], 0)

    def test_bloque_vlm_agrega(self):
        vlm = [
            {"enabled": True, "crops": 3, "signatures_resolved": 2,
             "fields_resolved": 1},
            {"enabled": True, "crops": 1, "signatures_resolved": 0,
             "fields_resolved": 1},
            {"enabled": False, "disabled": "sin casos inciertos"},
        ]
        stats = construir_stats(self.reports, vlm_stats=vlm)
        self.assertTrue(stats["vlm"]["activo"])
        self.assertEqual(stats["vlm"]["bitacoras_con_vlm"], 2)
        self.assertEqual(stats["vlm"]["crops_consultados"], 4)
        self.assertEqual(stats["vlm"]["firmas_resueltas"], 2)
        self.assertEqual(stats["vlm"]["campos_resueltos"], 2)


class TestEscribirStats(unittest.TestCase):
    def test_escribe_json_valido(self):
        reports = [_reporte("a.pdf", _page(1, "2147300", "HP-1534CMP",
                                           date="2026/07/15"))]
        with tempfile.TemporaryDirectory() as tmp:
            ruta = escribir_stats(reports, Path(tmp), corrida="corrida X",
                                  separar_por=["mes"])
            self.assertEqual(ruta, Path(tmp) / "stats.json")
            stats = json.loads(ruta.read_text(encoding="utf-8"))
        self.assertEqual(stats["corrida"], "corrida X")
        self.assertEqual(stats["por_mes"], {"2026-07": 1})
        self.assertTrue(stats["separacion"]["completa"])


class TestJsonConsolidado(unittest.TestCase):
    def test_mismo_nombre_que_el_csv(self):
        reports = [
            _reporte("a.pdf", _page(1, "2147300", "HP-1534CMP")),
            _reporte("b.pdf", _page(1, "2271600", "HP-1538CMP")),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            datos = Path(tmp) / "datos"
            ruta = JsonReporter().write_consolidated(
                reports, datos / "BITS 05 AUG 2026 00 23.json",
                corrida="BITS 05 AUG 2026 00 23",
            )
            self.assertEqual(ruta.name, "BITS 05 AUG 2026 00 23.json")
            payload = json.loads(ruta.read_text(encoding="utf-8"))
        self.assertEqual(payload["corrida"], "BITS 05 AUG 2026 00 23")
        self.assertEqual(payload["total_bitacoras"], 2)
        self.assertEqual(len(payload["reportes"]), 2)
        self.assertEqual(payload["reportes"][0]["pdf_path"], "a.pdf")


if __name__ == "__main__":
    unittest.main()

"""Pruebas de la clasificación de discrepancias (faltas de firma).

Reglas: vuelo requiere piloto + capitán + licencia del capitán;
mantenimiento (technician_license presente) requiere piloto + técnico.
Una licencia de técnico ilegible deja el tipo de página INCIERTO (no se
acusan los campos ambiguos). Las lecturas de baja confianza nunca se
acusan como faltas: categoría UNCERTAIN (revisión manual).
"""

from __future__ import annotations

import unittest
from pathlib import Path

from app.models.schemas import FieldResult, PageResult, Status, ValidationReport
from app.templates.manager import TemplateManager
from app.validation.discrepancias import (
    Categoria,
    TipoEntrada,
    clasificar_lote,
)

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = TemplateManager().load(
    ROOT / "app/templates/examples/aircraft_log.json"
)

# Umbrales por defecto de la plantilla
PRESENTE = 0.9  # >= sig_present_conf (0.45)
AUSENTE = 0.85  # >= sig_absent_conf (0.55)
DUDOSA = 0.2    # por debajo de ambos umbrales


def _sig(fid: str, value: str, conf: float) -> FieldResult:
    return FieldResult(page_number=1, field_id=fid, field_type="signature",
                       value=value, confidence=conf,
                       status=Status.OK if value == "true"
                       else Status.ERROR)


def _page(pn: int, log: str, mat: str, **sigs) -> PageResult:
    page = PageResult(page_number=pn)
    page.add_field(FieldResult(page_number=pn, field_id="log_number",
                               field_type="ocr", value=log,
                               confidence=1.0, status=Status.OK))
    page.add_field(FieldResult(page_number=pn, field_id="matricula",
                               field_type="ocr", value=mat,
                               confidence=1.0, status=Status.OK))
    for fid, (value, conf) in sigs.items():
        page.add_field(_sig(fid, value, conf))
    return page


def _vuelo_ok(pn: int = 1, log: str = "2147337", mat: str = "HP-1534CMP",
              **extra) -> PageResult:
    sigs = {
        "pilot_signature": ("true", PRESENTE),
        "captain_signature": ("true", PRESENTE),
        "captain_license": ("true", PRESENTE),
        "technician_signature": ("false", AUSENTE),
        "technician_license": ("false", AUSENTE),
    }
    sigs.update(extra)
    return _page(pn, log, mat, **sigs)


def _mant_ok(pn: int = 1, log: str = "2147337", mat: str = "HP-1534CMP",
             **extra) -> PageResult:
    sigs = {
        "pilot_signature": ("true", PRESENTE),
        "captain_signature": ("false", AUSENTE),
        "captain_license": ("false", AUSENTE),
        "technician_signature": ("true", PRESENTE),
        "technician_license": ("true", PRESENTE),
    }
    sigs.update(extra)
    return _page(pn, log, mat, **sigs)


def _reporte(*pages: PageResult) -> ValidationReport:
    return ValidationReport(pdf_path="fixture.pdf",
                            template_name=TEMPLATE.name, pages=list(pages))


class TestClasificacion(unittest.TestCase):
    def test_vuelo_completo_sin_discrepancia(self):
        self.assertEqual(clasificar_lote([_reporte(_vuelo_ok())], TEMPLATE),
                         [])

    def test_mantenimiento_completo_sin_discrepancia(self):
        self.assertEqual(clasificar_lote([_reporte(_mant_ok())], TEMPLATE),
                         [])

    def test_vuelo_sin_firma_de_capitan(self):
        pagina = _vuelo_ok(captain_signature=("false", AUSENTE))
        entradas = clasificar_lote([_reporte(pagina)], TEMPLATE)
        self.assertEqual(len(entradas), 1)
        entrada = entradas[0]
        self.assertIs(entrada.tipo, TipoEntrada.VUELO)
        self.assertIs(entrada.categoria, Categoria.MISSING)
        self.assertIn("Falta firma de capitán", entrada.razones())

    def test_vuelo_sin_licencia_de_capitan(self):
        pagina = _vuelo_ok(captain_license=("false", AUSENTE))
        entradas = clasificar_lote([_reporte(pagina)], TEMPLATE)
        self.assertIn("Falta licencia del capitán", entradas[0].razones())

    def test_vuelo_sin_firma_de_piloto(self):
        pagina = _vuelo_ok(pilot_signature=("false", AUSENTE))
        entradas = clasificar_lote([_reporte(pagina)], TEMPLATE)
        self.assertIn("Falta firma de piloto", entradas[0].razones())

    def test_mantenimiento_sin_firma_de_piloto(self):
        pagina = _mant_ok(pilot_signature=("false", AUSENTE))
        entradas = clasificar_lote([_reporte(pagina)], TEMPLATE)
        self.assertEqual(len(entradas), 1)
        self.assertIs(entradas[0].tipo, TipoEntrada.MANTENIMIENTO)
        self.assertIn("entrada de mantenimiento", entradas[0].razones()[0])

    def test_mantenimiento_sin_firma_de_tecnico(self):
        pagina = _mant_ok(technician_signature=("false", AUSENTE))
        entradas = clasificar_lote([_reporte(pagina)], TEMPLATE)
        self.assertEqual(len(entradas), 1)
        self.assertIs(entradas[0].tipo, TipoEntrada.MANTENIMIENTO)
        self.assertIs(entradas[0].categoria, Categoria.MISSING)
        self.assertIn(
            "Falta firma de técnico (entrada de mantenimiento)",
            entradas[0].razones(),
        )

    def test_mantenimiento_no_requiere_firmas_de_capitan(self):
        pagina = _page(
            1,
            "2147337",
            "HP-1534CMP",
            pilot_signature=("true", PRESENTE),
            technician_signature=("true", PRESENTE),
            technician_license=("true", PRESENTE),
        )
        self.assertEqual(clasificar_lote([_reporte(pagina)], TEMPLATE), [])

    def test_licencia_de_tecnico_dudosa_hace_tipo_incierto(self):
        # Una licencia de técnico con confianza baja no identifica
        # mantenimiento ni permite descartarlo: el tipo de página queda
        # INCIERTO y no se acusan los campos ambiguos (capitán/licencia).
        pagina = _page(1, "2147337", "HP-1534CMP",
                       pilot_signature=("true", PRESENTE),
                       captain_signature=("true", PRESENTE),
                       captain_license=("false", AUSENTE),
                       technician_signature=("true", PRESENTE),
                       technician_license=("true", DUDOSA))
        entradas = clasificar_lote([_reporte(pagina)], TEMPLATE)
        self.assertEqual(len(entradas), 1)
        self.assertIs(entradas[0].tipo, TipoEntrada.INCIERTO)
        self.assertIs(entradas[0].categoria, Categoria.UNCERTAIN)
        self.assertEqual(
            [c.field_id for c in entradas[0].campos],
            ["technician_license"],
        )

    def test_tipo_incierto_sin_firma_de_piloto_es_falta(self):
        # La firma de piloto se exige en ambas interpretaciones: en una
        # página INCIERTO su ausencia confirmada se reporta como falta.
        pagina = _page(1, "2147337", "HP-1534CMP",
                       pilot_signature=("false", AUSENTE),
                       captain_signature=("true", PRESENTE),
                       captain_license=("false", AUSENTE),
                       technician_signature=("true", PRESENTE),
                       technician_license=("true", DUDOSA))
        entradas = clasificar_lote([_reporte(pagina)], TEMPLATE)
        self.assertEqual(len(entradas), 1)
        self.assertIs(entradas[0].tipo, TipoEntrada.INCIERTO)
        self.assertIs(entradas[0].categoria, Categoria.MISSING)
        self.assertIn("Falta firma de piloto", entradas[0].razones()[1])

    def test_firma_de_tecnico_dudosa_en_mantenimiento_es_incierta(self):
        pagina = _mant_ok(technician_signature=("false", DUDOSA))
        entradas = clasificar_lote([_reporte(pagina)], TEMPLATE)
        self.assertEqual(len(entradas), 1)
        self.assertIs(entradas[0].tipo, TipoEntrada.MANTENIMIENTO)
        self.assertIs(entradas[0].categoria, Categoria.UNCERTAIN)
        self.assertIn(
            "Firma de técnico incierta (entrada de mantenimiento); revisar",
            entradas[0].razones(),
        )

    def test_confianza_baja_no_es_falta_sino_incierta(self):
        pagina = _vuelo_ok(pilot_signature=("false", DUDOSA))
        entradas = clasificar_lote([_reporte(pagina)], TEMPLATE)
        self.assertEqual(len(entradas), 1)
        self.assertIs(entradas[0].categoria, Categoria.UNCERTAIN)
        self.assertIn("revisar", entradas[0].razones()[0])

    def test_presencia_de_confianza_baja_tambien_es_incierta(self):
        pagina = _vuelo_ok(pilot_signature=("true", DUDOSA))
        entradas = clasificar_lote([_reporte(pagina)], TEMPLATE)
        self.assertEqual(len(entradas), 1)
        self.assertIs(entradas[0].categoria, Categoria.UNCERTAIN)
        self.assertIn("Firma de piloto incierta", entradas[0].razones()[0])

    def test_unclear_no_es_falta_sino_incierta(self):
        pagina = _vuelo_ok(captain_signature=("unclear", DUDOSA))
        entradas = clasificar_lote([_reporte(pagina)], TEMPLATE)
        self.assertIs(entradas[0].categoria, Categoria.UNCERTAIN)

    def test_categoria_missing_domina_a_uncertain(self):
        pagina = _vuelo_ok(pilot_signature=("false", DUDOSA),
                           captain_signature=("false", AUSENTE))
        entradas = clasificar_lote([_reporte(pagina)], TEMPLATE)
        self.assertIs(entradas[0].categoria, Categoria.MISSING)
        self.assertEqual(len(entradas[0].campos), 2)

    def test_pagina_en_blanco_se_ignora(self):
        pagina = _vuelo_ok()
        pagina.blank = True
        pagina.fields = []
        self.assertEqual(clasificar_lote([_reporte(pagina)], TEMPLATE), [])


class TestOrdenYMarcado(unittest.TestCase):
    def test_orden_por_avion_y_logpage(self):
        pages = [
            _vuelo_ok(1, "2147337", "HP-1538CMP",
                      captain_signature=("false", AUSENTE)),
            _vuelo_ok(2, "2147338", "HP-1534CMP",
                      captain_signature=("false", AUSENTE)),
            _vuelo_ok(3, "2271650", "HP-1538CMP",
                      captain_signature=("false", AUSENTE)),
            _vuelo_ok(4, "2147340", "HP-1534CMP",
                      captain_signature=("false", AUSENTE)),
        ]
        entradas = clasificar_lote([_reporte(*pages)], TEMPLATE)
        matriculas = [e.matricula for e in entradas]
        logs = [e.log_number for e in entradas]
        self.assertEqual(matriculas,
                         ["HP-1534CMP", "HP-1534CMP", "HP-1538CMP",
                          "HP-1538CMP"])
        self.assertEqual(logs, [2147338, 2147340, 2147337, 2271650])
        self.assertEqual(
            [(e.matricula, e.log_number) for e in entradas],
            [
                ("HP-1534CMP", 2147338),
                ("HP-1534CMP", 2147340),
                ("HP-1538CMP", 2147337),
                ("HP-1538CMP", 2271650),
            ],
        )

    def test_marca_discrepancy_en_pagina(self):
        pagina = _vuelo_ok(captain_signature=("false", AUSENTE))
        clasificar_lote([_reporte(pagina)], TEMPLATE)
        self.assertTrue(pagina.discrepancy)
        pagina_ok = _vuelo_ok()
        clasificar_lote([_reporte(pagina_ok)], TEMPLATE)
        self.assertFalse(pagina_ok.discrepancy)

    def test_sin_matricula_va_al_final(self):
        pagina = _page(1, "2147337", None,
                       pilot_signature=("true", PRESENTE),
                       captain_signature=("true", PRESENTE),
                       captain_license=("false", AUSENTE),
                       technician_signature=("false", AUSENTE),
                       technician_license=("false", AUSENTE))
        pagina2 = _vuelo_ok(2, "2147338", "HP-1534CMP",
                            captain_signature=("false", AUSENTE))
        entradas = clasificar_lote([_reporte(pagina2, pagina)], TEMPLATE)
        self.assertEqual(len(entradas), 2)
        self.assertEqual(entradas[0].matricula, "HP-1534CMP")
        self.assertIsNone(entradas[1].matricula)


if __name__ == "__main__":
    unittest.main()

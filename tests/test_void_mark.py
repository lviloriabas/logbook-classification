"""VOID exige una palabra grande confirmada y conserva los datos del indice."""

import numpy as np
import pytest

from app.models.schemas import OcrResult
from app.vision import void_mark
from app.validation.discrepancias import clasificar_lote
from tests.test_discrepancias import TEMPLATE, _corregida, _reporte


@pytest.mark.parametrize("texto,score,esperado", [
    ("VOID", .95, True), ("V01D", .90, True), ("VOlD", .85, True),
    ("VOID", .79, False), ("VOD", .99, False), ("GOLD", .99, False),
    ("COLOCAR VOID", .99, False), ("AVOID", .99, False), ("VOYD", .99, False),
])
def test_exige_las_cuatro_letras(texto, score, esperado):
    assert void_mark.es_lectura_void(texto, score) is esperado


def test_las_casillas_vacias_no_crean_evidencia_void():
    page = _corregida(correction_block=("false", .99))
    clasificar_lote([_reporte(page)], TEMPLATE)
    assert page.void_mark is None


def test_void_con_correccion_escrita_conserva_indice_y_no_reclama_firmas():
    page = _corregida()
    page.date = "2026-08-17"
    campos = [f.model_dump() for f in page.fields]
    assert clasificar_lote([_reporte(page)], TEMPLATE)
    page.void_mark = OcrResult(text="VOID", confidence=.92)
    assert clasificar_lote([_reporte(page)], TEMPLATE) == []
    assert not page.discrepancy and not page.discrepancy_fields
    assert page.date == "2026-08-17"
    assert [f.model_dump() for f in page.fields] == campos


def test_una_lectura_incierta_no_borra_la_discrepancia():
    page = _corregida()
    page.void_mark = OcrResult(text="VOID", confidence=.6)
    assert clasificar_lote([_reporte(page)], TEMPLATE)
    assert page.discrepancy


def test_sin_letras_grandes_no_invoca_el_reconocedor():
    class NoDebeLlamarse:
        def recognize_lines(self, _):
            raise AssertionError("No hay regiones para OCR")
    assert void_mark.detectar_void(np.full((800, 1000, 3), 255, np.uint8), NoDebeLlamarse()) is None


def test_exige_dos_lecturas_y_admite_cancelar(monkeypatch):
    monkeypatch.setattr(void_mark, "candidatos", lambda _: [
        (1, np.array([400., 300.]), 350., 160., -25.),
    ])
    class Motor:
        def __init__(self, cantidad):
            self.cantidad = cantidad
        def recognize_lines(self, _):
            return [[OcrResult(text="VOID", confidence=.92)] for _ in range(self.cantidad)]
    image = np.full((800, 1000, 3), 255, np.uint8)
    assert void_mark.detectar_void(image, Motor(1)) is None
    assert void_mark.detectar_void(image, Motor(2)).text == "VOID"
    assert void_mark.detectar_void(image, Motor(2), cancelado=lambda: True) is None

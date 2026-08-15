"""Pruebas de la detección de firmas sobre recortes sintéticos.

Los casos reproducen lo que trae de verdad el recorte de un campo de firma
en estas bitácoras y que antes provocaba fallos: el rótulo impreso dentro
del campo, la fotocopia gris sin escritura, la escritura clara sobre esa
misma fotocopia, la línea preimpresa y la calca de la página vecina.
"""

from __future__ import annotations

import unittest

import cv2
import numpy as np

from app.templates.schema import FieldTemplate
from app.vision.signature import UNCLEAR, detect_signature

ANCHO, ALTO = 360, 90


def _campo(**kw) -> FieldTemplate:
    valores = dict(id="firma", type="signature", required=True,
                   x=0, y=0, w=1, h=1)
    valores.update(kw)
    return FieldTemplate(**valores)


def _papel(gris: int = 255) -> np.ndarray:
    """Hoja limpia; ``gris`` simula el fondo de una fotocopia."""
    return np.full((ALTO, ANCHO, 3), gris, dtype=np.uint8)


def _linea_impresa(img: np.ndarray, gris: int = 90) -> np.ndarray:
    """Línea preimpresa sobre la que se firma."""
    cv2.line(img, (5, ALTO - 25), (ANCHO - 5, ALTO - 25), (gris,) * 3, 2)
    return img


def _rotulo(img: np.ndarray) -> np.ndarray:
    """Rótulo impreso del formulario, gris claro y de letra pequeña."""
    cv2.putText(img, "(XII) CAPTAIN SIGNATURE", (70, ALTO - 10),
                cv2.FONT_HERSHEY_SIMPLEX, 0.32, (150,) * 3, 1)
    return img


def _rubrica(img: np.ndarray, tinta: int = 25) -> np.ndarray:
    """Rúbrica manuscrita: trazos largos y solapados."""
    for dx in (0, 55, 110):
        cv2.ellipse(img, (70 + dx, ALTO // 2), (42, 20), 20, 0, 300,
                    (tinta,) * 3, 3)
    cv2.line(img, (30, ALTO - 20), (250, 25), (tinta,) * 3, 3)
    return img


def _numero_licencia(img: np.ndarray) -> np.ndarray:
    """Número de licencia: dígitos sueltos repartidos a lo ancho."""
    cv2.putText(img, "8-805-1A7", (20, ALTO - 30),
                cv2.FONT_HERSHEY_SIMPLEX, 1.4, (25, 25, 25), 3)
    return img


class TestFirmaPresente(unittest.TestCase):
    def test_rubrica_sobre_papel_limpio(self):
        campo = _campo()
        resultado = detect_signature(_rubrica(_papel()), campo, 1)
        self.assertEqual(resultado.value, "true")
        self.assertGreaterEqual(resultado.confidence, campo.sig_present_conf)
        self.assertEqual(resultado.status.value, "OK")

    def test_rubrica_cruzando_la_linea_preimpresa(self):
        img = _rubrica(_linea_impresa(_papel()))
        self.assertEqual(detect_signature(img, _campo(), 1).value, "true")

    def test_escritura_clara_sobre_fotocopia_gris(self):
        """El fondo gris no debe tapar la escritura: se mide contra el papel
        local, no contra un umbral global."""
        img = _rubrica(_linea_impresa(_papel(gris=200)), tinta=60)
        self.assertEqual(detect_signature(img, _campo(), 1).value, "true")

    def test_numero_de_licencia_disperso(self):
        """Los dígitos sueltos no concentran tinta como una rúbrica, pero se
        reparten a lo ancho del campo."""
        img = _numero_licencia(_linea_impresa(_papel()))
        self.assertEqual(detect_signature(img, _campo(), 1).value, "true")

    def test_rubrica_de_boligrafo_azul_claro(self):
        """El gris aplana un azul claro hasta confundirlo con el papel; la
        tinta se mide sobre el canal donde es más oscura."""
        img = _linea_impresa(_papel())
        for dx in (0, 55, 110):
            cv2.ellipse(img, (70 + dx, ALTO // 2), (42, 20), 20, 0, 300,
                        (205, 90, 40), 3)
        self.assertEqual(detect_signature(img, _campo(), 1).value, "true")

    def test_confianza_crece_con_la_evidencia(self):
        floja = detect_signature(_numero_licencia(_papel()), _campo(), 1)
        fuerte = detect_signature(_rubrica(_papel()), _campo(), 1)
        self.assertEqual(floja.value, "true")
        self.assertEqual(fuerte.value, "true")
        self.assertLess(floja.confidence, fuerte.confidence)

    def test_misma_decision_al_doble_de_resolucion(self):
        """Los umbrales son densidades, no cuentas de píxeles: doblar el DPI
        no cambia el veredicto."""
        img = _rubrica(_linea_impresa(_papel()))
        grande = cv2.resize(img, None, fx=2.0, fy=2.0,
                            interpolation=cv2.INTER_CUBIC)
        self.assertEqual(detect_signature(img, _campo(), 1, dpi=200).value,
                         detect_signature(grande, _campo(), 1, dpi=400).value)


class TestFirmaAusente(unittest.TestCase):
    def test_papel_limpio(self):
        resultado = detect_signature(_papel(), _campo(), 1)
        self.assertEqual(resultado.value, "false")
        self.assertGreaterEqual(resultado.confidence, _campo().sig_absent_conf)

    def test_solo_linea_preimpresa(self):
        resultado = detect_signature(_linea_impresa(_papel()), _campo(), 1)
        self.assertEqual(resultado.value, "false")
        self.assertGreaterEqual(resultado.confidence, _campo().sig_absent_conf)

    def test_rotulo_impreso_no_es_firma(self):
        """El rótulo del formulario cae dentro del recorte y antes se
        contaba como firma."""
        img = _rotulo(_linea_impresa(_papel()))
        self.assertEqual(detect_signature(img, _campo(), 1).value, "false")

    def test_fotocopia_gris_sin_escritura(self):
        img = _rotulo(_linea_impresa(_papel(gris=195)))
        self.assertEqual(detect_signature(img, _campo(), 1).value, "false")

    def test_speckles_de_escaneo(self):
        img = _papel()
        rng = np.random.default_rng(42)
        for yi, xi in zip(rng.integers(2, ALTO - 2, size=60),
                          rng.integers(2, ANCHO - 2, size=60)):
            cv2.circle(img, (int(xi), int(yi)), 1, (60, 60, 60), -1)
        self.assertNotEqual(detect_signature(img, _campo(), 1).value, "true")

    def test_ausencia_es_error_solo_si_el_campo_es_obligatorio(self):
        obligatorio = detect_signature(_papel(), _campo(required=True), 1)
        opcional = detect_signature(_papel(), _campo(required=False), 1)
        self.assertEqual(obligatorio.status.value, "ERROR")
        self.assertEqual(opcional.status.value, "WARNING")


class TestFirmaIncierta(unittest.TestCase):
    """Evidencia que no alcanza para afirmar ni para negar.

    Nunca debe acusarse una falta con estas entradas: quedan en WARNING
    para revisión manual (o para que las arbitre el verificador VLM).
    """

    def test_calca_de_la_pagina_vecina(self):
        """Arcos finos y largos que atraviesan el campo: hay tinta, pero no
        tiene forma de escritura."""
        img = _papel(gris=205)
        for cx in (60, 180, 300):
            cv2.ellipse(img, (cx, ALTO), (55, 40), 0, 180, 360, (95,) * 3, 2)
        resultado = detect_signature(img, _campo(), 1)
        self.assertEqual(resultado.value, UNCLEAR)
        self.assertEqual(resultado.status.value, "WARNING")

    def test_escritura_que_se_sale_del_campo(self):
        """Solo entra la base de unos dígitos escritos demasiado arriba."""
        img = _linea_impresa(_papel())
        cv2.putText(img, "0400", (90, 14), cv2.FONT_HERSHEY_SIMPLEX, 1.0,
                    (30, 30, 30), 3)
        self.assertEqual(detect_signature(img, _campo(), 1).value, UNCLEAR)

    def test_sello_impreso_en_el_campo(self):
        img = _linea_impresa(_papel())
        cv2.rectangle(img, (15, 12), (95, 45), (60, 60, 60), 2)
        cv2.putText(img, "STA", (20, 38), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    (90, 90, 90), 1)
        self.assertEqual(detect_signature(img, _campo(), 1).value, UNCLEAR)

    def test_recorte_quemado_es_indeterminable(self):
        """Sin papel blanco no hay contra qué medir la tinta."""
        rng = np.random.default_rng(7)
        img = _papel()
        img[:, :, :] = rng.integers(18, 23, size=(ALTO, ANCHO),
                                    dtype=np.uint8)[:, :, None]
        resultado = detect_signature(img, _campo(max_ink_ratio=0.60), 1)
        self.assertEqual(resultado.value, UNCLEAR)
        self.assertLess(resultado.confidence, _campo().sig_present_conf)


if __name__ == "__main__":
    unittest.main()

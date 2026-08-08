"""Pruebas de la detección de firmas sobre imágenes sintéticas.

Casos: región limpia (ausente), trazos de escritura (presente), ruido de
speckles (no falso positivo), banda oscura/deteriorada (ausente, confianza
baja) y evidencia parcial (estado 'unclear').
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

import cv2
import numpy as np

import app.vision.signature as signature
from app.templates.schema import FieldTemplate
from app.vision.signature import UNCLEAR, detect_signature

ANCHO, ALTO = 300, 80


def _campo(**kw) -> FieldTemplate:
    valores = dict(
        id="firma", type="signature", required=True,
        x=0, y=0, w=1, h=1,
        min_texture_ink=0.004,
        min_components=2,
        min_component_area=6.0,
    )
    valores.update(kw)
    return FieldTemplate(**valores)


def _blanca() -> np.ndarray:
    return np.full((ALTO, ANCHO, 3), 255, dtype=np.uint8)


def _firma() -> np.ndarray:
    """Región con un garabato de 3 trazos gruesos (firma sintética)."""
    img = _blanca()
    for offset in (10, 40, 70):
        cv2.ellipse(img, (offset + 30, ALTO // 2), (45, 12), 15, 0, 300,
                    (20, 20, 20), 6)
    return img


def _speckles() -> np.ndarray:
    """Región limpia con puntos aislados de ruido (1-2 px)."""
    img = _blanca()
    rng = np.random.default_rng(42)
    y, x = rng.integers(2, ALTO - 2, size=40), rng.integers(2, ANCHO - 2,
                                                            size=40)
    for yi, xi in zip(y, x):
        cv2.circle(img, (int(xi), int(yi)), 1, (90, 90, 90), -1)
    return img


def _oscura() -> np.ndarray:
    """Región deteriorada: banda muy oscura sin trazos distinguibles.

    Valores tan bajos (18-22) dejan el umbral Otsu fuera del rango
    [30, 230] y se usa el respaldo fijo, con lo que la fracción de tinta
    supera ``max_ink_ratio`` y la región se trata como oscura.
    """
    img = _blanca()
    rng = np.random.default_rng(7)
    banda = rng.integers(18, 23, size=(50, ANCHO)).astype(np.uint8)
    img[15:65, :, :] = banda[:, :, None]
    return img


def _parcial() -> np.ndarray:
    """Evidencia parcial: un solo trazo corto y fino (firma muy débil).

    Fino (2 px) para que la tinta texturizada quede por debajo del
    factor 2.5x del umbral y no se active la ruta del trazo dominante.
    """
    img = _blanca()
    cv2.line(img, (30, ALTO // 2), (80, ALTO // 2 + 4), (25, 25, 25), 2)
    return img


class TestDetectSignature(unittest.TestCase):
    def test_region_limpia_ausente(self):
        campo = _campo()
        resultado = detect_signature(_blanca(), campo, 1)
        self.assertEqual(resultado.value, "false")
        self.assertGreaterEqual(resultado.confidence, 0.5)

    def test_firma_presente(self):
        campo = _campo()
        resultado = detect_signature(_firma(), campo, 1)
        self.assertEqual(resultado.value, "true")
        self.assertGreaterEqual(resultado.confidence, 0.45)

    def test_firma_marginal_tiene_confianza_baja(self):
        campo = _campo()
        with patch.object(signature, "_textured_ink", return_value=0.004), \
                patch.object(signature, "_significant_strokes",
                             return_value=(2, 100.0)):
            resultado = detect_signature(_blanca(), campo, 1)

        self.assertEqual(resultado.value, "true")
        self.assertLess(resultado.confidence, campo.sig_present_conf)

    def test_confianza_de_presencia_crece_con_la_evidencia(self):
        campo = _campo()
        with patch.object(signature, "_textured_ink", return_value=0.008), \
                patch.object(signature, "_significant_strokes",
                             return_value=(2, 100.0)):
            marginal = detect_signature(_blanca(), campo, 1)
        with patch.object(signature, "_textured_ink", return_value=0.040), \
                patch.object(signature, "_significant_strokes",
                             return_value=(2, 100.0)):
            fuerte = detect_signature(_blanca(), campo, 1)

        self.assertEqual(marginal.value, "true")
        self.assertEqual(fuerte.value, "true")
        self.assertLess(marginal.confidence, fuerte.confidence)

    def test_ausencia_casi_firma_queda_incierta(self):
        campo = _campo()
        with patch.object(signature, "_textured_ink", return_value=0.0036), \
                patch.object(signature, "_significant_strokes",
                             return_value=(0, 0.0)):
            resultado = detect_signature(_blanca(), campo, 1)

        self.assertEqual(resultado.value, "false")
        self.assertLess(resultado.confidence, campo.sig_absent_conf)

    def test_ausencia_limpia_tiene_mas_confianza(self):
        campo = _campo()
        limpia = detect_signature(_blanca(), campo, 1)
        with patch.object(signature, "_textured_ink", return_value=0.0036), \
                patch.object(signature, "_significant_strokes",
                             return_value=(0, 0.0)):
            marginal = detect_signature(_blanca(), campo, 1)

        self.assertGreater(limpia.confidence, marginal.confidence)
        self.assertGreaterEqual(limpia.confidence, campo.sig_absent_conf)

    def test_speckles_no_falso_positivo(self):
        campo = _campo()
        resultado = detect_signature(_speckles(), campo, 1)
        self.assertNotEqual(resultado.value, "true")

    def test_banda_oscura_ausente_sin_confianza(self):
        campo = _campo(max_ink_ratio=0.9)
        resultado = detect_signature(_oscura(), campo, 1)
        self.assertEqual(resultado.value, "false")
        self.assertLess(resultado.confidence, 0.45)

    def test_evidencia_parcial_unclear(self):
        campo = _campo()
        resultado = detect_signature(_parcial(), campo, 1)
        self.assertEqual(resultado.value, UNCLEAR)
        self.assertEqual(resultado.status.value, "WARNING")

    def test_ausente_requerido_error(self):
        resultado = detect_signature(_blanca(), _campo(required=True), 1)
        self.assertEqual(resultado.status.value, "ERROR")
        resultado_opcional = detect_signature(
            _blanca(), _campo(required=False), 1
        )
        self.assertEqual(resultado_opcional.status.value, "WARNING")

    def test_banda_restringida_ignora_bordes(self):
        # Etiqueta impresa solo fuera de la banda (zona_top alta): la firma
        # en la banda sigue detectándose.
        img = _firma()
        cv2.putText(img, "FIRMA PILOTO", (10, 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (30, 30, 30), 2)
        campo = _campo(zone_top=0.3, zone_bottom=0.9)
        self.assertEqual(detect_signature(img, campo, 1).value, "true")


if __name__ == "__main__":
    unittest.main()

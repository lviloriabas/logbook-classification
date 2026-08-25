"""Lectura sin detector de texto (``ocr_mode='line'``) y su red de seguridad.

El detector es la fase cara del OCR (646 ms por recorte frente a 175 ms del
reconocedor). Los campos cuyo recorte ya contiene un único valor pueden
saltárselo, pero solo si una lectura inservible se vuelve a intentar con el
pipeline completo.
"""

from __future__ import annotations

import unittest

import numpy as np

from app.models.schemas import OcrResult
from app.ocr.regional import ocr_regions
from app.templates.schema import FieldTemplate, OcrMode


def _field(field_id: str, mode: OcrMode, **kwargs) -> FieldTemplate:
    return FieldTemplate(
        id=field_id, x=0.1, y=0.1, w=0.2, h=0.1, ocr_mode=mode, **kwargs
    )


class FakeEngine:
    """Motor que registra por qué ruta se leyó cada batch."""

    name = "fake"

    def __init__(self, line_texts=None, detect_texts=None) -> None:
        self.line_texts = line_texts or {}
        self.detect_texts = detect_texts or {}
        self.line_calls: list[int] = []
        self.detect_calls: list[int] = []

    def _reply(self, table, images, calls):
        calls.append(len(images))
        results = []
        for index in range(len(images)):
            text = table.get(len(results) + index, table.get("*", ""))
            results.append(
                [OcrResult(text=text, confidence=0.9)] if text else []
            )
        return results

    def recognize_batch(self, images):
        return self._reply(self.detect_texts, images, self.detect_calls)

    def recognize_lines(self, images):
        return self._reply(self.line_texts, images, self.line_calls)


class LegacyEngine:
    """Motor sin fase de reconocimiento separada."""

    name = "legacy"

    def __init__(self) -> None:
        self.detect_calls: list[int] = []

    def recognize_batch(self, images):
        self.detect_calls.append(len(images))
        return [[OcrResult(text="OK", confidence=0.8)] for _ in images]


PAGE = np.full((400, 400, 3), 255, np.uint8)


class TestLineMode(unittest.TestCase):
    def test_cada_modo_usa_su_ruta(self):
        engine = FakeEngine(
            line_texts={"*": "111"}, detect_texts={"*": "222"}
        )
        fields = [
            _field("rapido", OcrMode.LINE),
            _field("completo", OcrMode.DETECT),
        ]
        results = ocr_regions(engine, PAGE, fields, preprocess=False)
        self.assertEqual(results[0][0], "111")
        self.assertEqual(results[1][0], "222")
        self.assertEqual(engine.line_calls, [1])
        self.assertEqual(engine.detect_calls, [1])

    def test_por_defecto_se_usa_el_detector(self):
        """Una plantilla sin ``ocr_mode`` conserva el comportamiento previo."""
        engine = FakeEngine(detect_texts={"*": "222"})
        field = FieldTemplate(id="clasico", x=0.1, y=0.1, w=0.2, h=0.1)
        self.assertIs(field.ocr_mode, OcrMode.DETECT)
        ocr_regions(engine, PAGE, [field], preprocess=False)
        self.assertEqual(engine.line_calls, [])
        self.assertEqual(engine.detect_calls, [1])

    def test_una_lectura_rechazada_se_relee_con_detector(self):
        engine = FakeEngine(
            line_texts={"*": "basura"}, detect_texts={"*": "1234567"}
        )
        field = _field("log_number", OcrMode.LINE, regex=r"^\d{7}$")

        def accept(_field, text, _confidence):
            return bool(text) and text.isdigit()

        results = ocr_regions(
            engine, PAGE, [field], preprocess=False, accept=accept
        )
        self.assertEqual(results[0][0], "1234567")
        self.assertEqual(engine.line_calls, [1])
        self.assertEqual(engine.detect_calls, [1])

    def test_una_lectura_aceptada_no_paga_el_detector(self):
        engine = FakeEngine(
            line_texts={"*": "1234567"}, detect_texts={"*": "9999999"}
        )
        field = _field("log_number", OcrMode.LINE, regex=r"^\d{7}$")
        results = ocr_regions(
            engine, PAGE, [field], preprocess=False,
            accept=lambda _f, text, _c: text.isdigit(),
        )
        self.assertEqual(results[0][0], "1234567")
        self.assertEqual(engine.detect_calls, [])

    def test_sin_predicado_no_hay_reintento(self):
        engine = FakeEngine(line_texts={"*": ""}, detect_texts={"*": "x"})
        field = _field("suelto", OcrMode.LINE)
        ocr_regions(engine, PAGE, [field], preprocess=False)
        self.assertEqual(engine.detect_calls, [])

    def test_motor_sin_recognize_lines_no_falla(self):
        engine = LegacyEngine()
        fields = [_field("a", OcrMode.LINE), _field("b", OcrMode.DETECT)]
        results = ocr_regions(engine, PAGE, fields, preprocess=False)
        self.assertEqual([text for text, _c in results], ["OK", "OK"])

    def test_se_conserva_el_orden_de_los_campos(self):
        engine = FakeEngine(
            line_texts={"*": "L"}, detect_texts={"*": "D"}
        )
        fields = [
            _field("d1", OcrMode.DETECT),
            _field("l1", OcrMode.LINE),
            _field("d2", OcrMode.DETECT),
            _field("l2", OcrMode.LINE),
        ]
        results = ocr_regions(engine, PAGE, fields, preprocess=False)
        self.assertEqual([text for text, _c in results], ["D", "L", "D", "L"])


class TestAcceptPredicate(unittest.TestCase):
    """El predicado real del pipeline valida postproceso y regla del campo."""

    def setUp(self) -> None:
        from app.core.pipeline import _accept_line_reading

        self.accept = _accept_line_reading

    def test_rechaza_vacio(self):
        field = _field("log_number", OcrMode.LINE, postprocess="digits")
        self.assertFalse(self.accept(field, "", 0.9))

    def test_rechaza_lo_que_incumple_la_regla(self):
        field = _field(
            "log_number", OcrMode.LINE, postprocess="digits",
            regex=r"^\d{7}$",
        )
        self.assertFalse(self.accept(field, "123", 0.9))
        self.assertTrue(self.accept(field, "2301924", 0.9))

    def test_rechaza_celda_de_caracter_invalida(self):
        field = _field("day_1", OcrMode.LINE, postprocess="char",
                       regex=r"^\d$")
        self.assertFalse(self.accept(field, "工", 0.9))
        self.assertTrue(self.accept(field, "4", 0.9))


if __name__ == "__main__":
    unittest.main()

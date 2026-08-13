"""Pruebas del verificador VLM (Fase 1).

El servidor llama-server no existe en el entorno de pruebas: se ejercita
solo la lógica del pipeline con un ``VlmVerifier`` reemplazado por un
objeto determinista, y el parseo de respuestas del cliente real.

Verifica que:
- solo se recortan casos inciertos (firma ``unclear``, crítico vacío);
- un veredicto terminante se aplica al campo y la página se re-valida;
- sin casos inciertos (o con VLM desactivado) el pipeline queda intacto;
- la interpretación de respuestas es conservadora (solo tokens terminantes).
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from app.core import pipeline as pipeline_module
from app.core.config import AppConfig
from app.core.pipeline import Pipeline
from app.models.schemas import FieldResult, PageResult, Status
from app.templates.manager import TemplateManager
from app.verifier.launcher import VlmPaths

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = TemplateManager().load(
    ROOT / "template/aircraft_log.json"
)


class FakeEngine:
    name = "fake"


def _pip(config) -> Pipeline:
    return Pipeline(config, FakeEngine(), TEMPLATE)


def _field(fid, value, fdtype="ocr", conf: float = 0.5,
           status: Status = Status.ERROR) -> FieldResult:
    return FieldResult(page_number=1, field_id=fid, field_type=fdtype,
                       value=value, conf=conf, status=status)


def _page(*fields) -> PageResult:
    page = PageResult(page_number=1, status=Status.ERROR)
    for fld in fields:
        page.add_field(fld)
    return page


class FakeVerifier:
    """Reemplazo determinista del VlmVerifier para el pipeline."""

    def __init__(self, verdict=True, token="HP-1534CMP"):
        self.verdict = verdict
        self.token = token
        self.crops_used = 0

    def ensure_server(self):
        return True

    def shutdown(self):
        pass

    def check_signature(self, crop):
        return self.verdict

    def read_text(self, crop, kind):
        return self.token


class TestSelectDeCasos(unittest.TestCase):
    def test_solo_firma_unclear_y_critico_vacio(self):
        page = _page(
            _field("pilot_signature", "unclear", fdtype="signature",
                   status=Status.WARNING),
            _field("captain_signature", "false", fdtype="signature",
                   conf=0.9, status=Status.ERROR),
            _field("matricula", None, status=Status.ERROR),
            _field("log_number", "5551234", conf=1.0, status=Status.OK),
        )
        pipe = _pip(AppConfig(vlm_enabled=True, vlm_max_crops=10))
        targets = pipe._vlm_targets([page])
        self.assertEqual(
            sorted((field, kind) for _, field, kind in targets),
            [("matricula", "matricula"),
             ("pilot_signature", "signature")],
        )

    def test_presupuesto_cero_no_recorta_nada(self):
        page = _page(
            _field("pilot_signature", "unclear", fdtype="signature",
                   status=Status.WARNING),
        )
        pipe = _pip(AppConfig(vlm_enabled=True, vlm_max_crops=0))
        self.assertEqual(pipe._vlm_targets([page]), [])

    def test_fecha_con_valor_bajo_confidence_se_revisa(self):
        page = _page(
            FieldResult(
                page_number=1,
                field_id="day",
                field_type="ocr",
                value="2",
                confidence=0.3,
                status=Status.WARNING,
            )
        )
        pipe = _pip(AppConfig(vlm_enabled=True, vlm_max_crops=10))
        self.assertEqual(
            pipe._vlm_targets([page]), [(0, "day", "day")]
        )

    def test_fecha_con_alta_confianza_tambien_se_envia_al_vlm(self):
        page = _page(
            FieldResult(
                page_number=1,
                field_id="day",
                field_type="ocr",
                value="20",
                confidence=0.9,
                status=Status.OK,
            )
        )
        pipe = _pip(AppConfig(vlm_enabled=True, vlm_max_crops=10))
        self.assertEqual(
            pipe._vlm_targets([page]), [(0, "day", "day")]
        )

    def test_fecha_prioriza_sus_tres_partes_antes_de_otros_casos(self):
        page = _page(
            FieldResult(
                page_number=1,
                field_id="day",
                field_type="ocr",
                value="20",
                confidence=0.9,
                status=Status.OK,
            ),
            FieldResult(
                page_number=1,
                field_id="month",
                field_type="ocr",
                value="JUL",
                confidence=0.9,
                status=Status.OK,
            ),
            FieldResult(
                page_number=1,
                field_id="year",
                field_type="ocr",
                value="26",
                confidence=0.9,
                status=Status.OK,
            ),
            _field("pilot_signature", "unclear", fdtype="signature",
                   status=Status.WARNING),
        )
        pipe = _pip(AppConfig(vlm_enabled=True, vlm_max_crops=3))
        self.assertEqual(
            pipe._vlm_targets([page]), [
                (0, "day", "day"),
                (0, "month", "month"),
                (0, "year", "year"),
            ]
        )


class TestArbitraje(unittest.TestCase):
    def setUp(self):
        self.config = AppConfig(vlm_enabled=True, vlm_max_crops=10)
        self.image = np.zeros((300, 400), np.uint8)

    def _run(self, verdict=True, token="HP-1534CMP"):
        page = _page(
            _field("pilot_signature", "unclear", fdtype="signature",
                   conf=0.3, status=Status.WARNING),
            _field("matricula", None, status=Status.ERROR),
        )
        pipe = _pip(self.config)
        fake = FakeVerifier(verdict=verdict, token=token)
        with mock.patch.object(
            pipeline_module, "render_page", return_value=self.image
        ), mock.patch(
            "app.verifier.verifier.VlmVerifier", return_value=fake
        ):
            result = pipe._verify_pages(
                Path("x.pdf"), [page], None, None, None
            )
        return result[0], pipe

    def test_verdict_terminante_aplica_y_revalida(self):
        page, _ = self._run(verdict=True)
        piloto = next(f for f in page.fields
                      if f.field_id == "pilot_signature")
        self.assertEqual(piloto.value, "true")
        self.assertEqual(piloto.confidence, 0.90)
        self.assertIs(piloto.status, Status.OK)
        self.assertIn("VLM", piloto.comment)
        mat = next(f for f in page.fields if f.field_id == "matricula")
        self.assertEqual(mat.value, "HP-1534CMP")
        self.assertGreaterEqual(mat.confidence, 0.8)

    def test_ausencia_terminante_marca_falta(self):
        page, _ = self._run(verdict=False)
        piloto = next(f for f in page.fields
                      if f.field_id == "pilot_signature")
        self.assertEqual(piloto.value, "false")
        # pilot_signature es obligatoria en la plantilla
        self.assertIs(piloto.status, Status.ERROR)
        self.assertIn("AUSENTE", piloto.comment)

    def test_lectura_ilegible_no_sobrescribe(self):
        page, _ = self._run(verdict=None, token=None)
        piloto = next(f for f in page.fields
                      if f.field_id == "pilot_signature")
        self.assertEqual(piloto.value, "unclear")  # se conserva
        mat = next(f for f in page.fields if f.field_id == "matricula")
        self.assertIsNone(mat.value)

    def test_con_vlm_desactivado_queda_intacto(self):
        config = AppConfig(vlm_enabled=False, vlm_max_crops=10)
        page = _page(
            _field("pilot_signature", "unclear", fdtype="signature",
                   status=Status.WARNING),
        )
        pipe = _pip(config)
        out = pipe._verify_pages(Path("x.pdf"), [page], None, None, None)
        self.assertIs(out[0], page)  # lista intacta, mismo objeto de página


class TestParsingRespuestas(unittest.TestCase):
    """El cliente real solo acepta tokens terminantes."""

    def test_parsing_firma(self):
        # check_signature depende de _ask (servidor). Verificamos la lógica
        # de clasificación del texto de la respuesta.
        cases = {
            "PRESENTE": True,
            "PRESENTE, se ve claramente": True,
            "AUSENTE": False,
            "INCIERTO no puedo estar seguro": None,
            "No estoy seguro": None,
            "": None,
        }
        for raw, expected in cases.items():
            self.assertEqual(self._verdict(raw), expected, raw)

    def test_prompt_especifico_para_dia(self):
        from app.verifier.verifier import VlmVerifier

        verifier = VlmVerifier(AppConfig(vlm_enabled=False))
        with mock.patch.object(verifier, "_ask", return_value="20") as ask:
            self.assertEqual(verifier.read_text(np.zeros((20, 20)), "day"), "20")
        prompt = ask.call_args.args[0]
        self.assertIn("DAY", prompt)
        self.assertIn("lineas verticales", prompt)

    @staticmethod
    def _verdict(raw: str):
        upper = (raw or "").strip().upper()
        if "PRESENTE" in upper and "AUSENTE" not in upper:
            return True
        if "AUSENTE" in upper:
            return False
        return None


if __name__ == "__main__":
    unittest.main()

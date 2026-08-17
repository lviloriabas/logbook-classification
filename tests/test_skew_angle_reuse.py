"""El angulo de deskew se mide una vez, en la calibracion, y se reutiliza.

Detectar la inclinacion (Canny + Hough) cuesta 103-139 ms sobre la pagina a
DPI completo, y la calibracion ya la midio sobre la misma pagina a la mitad
de resolucion. Estas pruebas fijan que el angulo viaje desde la calibracion
hasta las tres etapas que lo necesitan -OCR, repaso de firmas y VLM- y que
ninguna vuelva a buscarlo por su cuenta.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from app.core import pipeline as pipeline_module
from app.core.config import AppConfig
from app.core.pipeline import Pipeline, process_page_image
from app.models.schemas import PageResult
from app.templates.schema import Template
from app.vision.alignment import TransformResult


class FakeEngine:
    name = "fake"

    def recognize_batch(self, images):
        return [[] for _ in images]

    def recognize_lines(self, images):
        return [[] for _ in images]


def _page(height: int = 60, width: int = 80) -> np.ndarray:
    """Pagina con textura suficiente para no darse por vacia."""
    rng = np.random.default_rng(7)
    return rng.integers(0, 255, (height, width, 3), dtype=np.uint8)


class TestProcessPageImageUsesGivenAngle(unittest.TestCase):
    def setUp(self):
        self.config = AppConfig(deskew=True, align=False, vlm_enabled=False)
        self.template = Template(name="fixture")

    def test_given_angle_skips_detection(self):
        with patch.object(pipeline_module, "deskew") as detect, \
                patch.object(pipeline_module, "rotate",
                             side_effect=lambda image, angle: image) as turn:
            page = process_page_image(
                _page(), 1, self.config, FakeEngine(), self.template, None,
                skew_angle=1.25,
            )

        detect.assert_not_called()
        turn.assert_called_once()
        self.assertAlmostEqual(turn.call_args.args[1], 1.25)
        self.assertAlmostEqual(page.skew_angle, 1.25)

    def test_zero_angle_rotates_nothing(self):
        with patch.object(pipeline_module, "deskew") as detect, \
                patch.object(pipeline_module, "rotate") as turn:
            page = process_page_image(
                _page(), 1, self.config, FakeEngine(), self.template, None,
                skew_angle=0.0,
            )

        detect.assert_not_called()
        turn.assert_not_called()
        self.assertEqual(page.skew_angle, 0.0)

    def test_without_angle_it_still_detects(self):
        with patch.object(pipeline_module, "deskew",
                          return_value=(_page(), 0.7)) as detect:
            page = process_page_image(
                _page(), 1, self.config, FakeEngine(), self.template, None,
            )

        detect.assert_called_once()
        self.assertAlmostEqual(page.skew_angle, 0.7)


class TestCalibrationPublishesAngles(unittest.TestCase):
    def setUp(self):
        self.pipeline = Pipeline(
            AppConfig(deskew=True, align=True, remove_printed=False),
            FakeEngine(),
            Template(name="fixture"),
        )

    def test_calibration_stores_one_angle_per_page(self):
        angles = iter([0.4, -0.9, 0.0])
        with patch.object(pipeline_module, "render_page",
                          return_value=_page()), \
                patch.object(pipeline_module, "deskew",
                             side_effect=lambda img: (img, next(angles))), \
                patch.object(pipeline_module, "compute_similarity_transform",
                             return_value=TransformResult(reliable=True)):
            self.pipeline._calibrate_impl(
                Path("fixture.pdf"), 1, 3, _page()
            )

        self.assertEqual(self.pipeline._skew_angles, [0.4, -0.9, 0.0])

    def test_sequential_forwards_the_angle_of_each_page(self):
        self.pipeline._skew_angles = [0.4, -0.9]
        seen = []

        def fake_process(*args, **kwargs):
            seen.append(kwargs["skew_angle"])
            return PageResult(page_number=1)

        with patch.object(pipeline_module, "render_page",
                          return_value=_page()), \
                patch.object(pipeline_module, "process_page_image",
                             fake_process):
            self.pipeline._process_sequential(
                Path("fixture.pdf"), 1, 2, _page()
            )

        self.assertEqual(seen, [0.4, -0.9])

    def test_pages_beyond_the_measured_range_detect_their_own(self):
        self.pipeline._skew_angles = [0.4]
        self.assertAlmostEqual(self.pipeline._skew_angle_at(0), 0.4)
        self.assertIsNone(self.pipeline._skew_angle_at(1))
        self.assertIsNone(self.pipeline._skew_angle_at(-1))


class TestWorkerReceivesAngles(unittest.TestCase):
    def test_worker_indexes_the_angle_from_the_first_page(self):
        pipeline_module._WORKER_STATE.clear()
        pipeline_module._WORKER_STATE.update({
            "pdf_path": Path("fixture.pdf"),
            "config": AppConfig(align=False),
            "engine": FakeEngine(),
            "template": Template(name="fixture"),
            "reference": None,
            "transforms": [],
            "own_reliability": [],
            "skew_angles": [0.1, 0.2, 0.3],
            "first_page": 5,
        })
        captured = {}

        def fake_process(*args, **kwargs):
            captured.update(kwargs)
            return PageResult(page_number=6)

        with patch.object(pipeline_module, "process_page", fake_process):
            pipeline_module._process_page_worker(6)

        self.assertAlmostEqual(captured["skew_angle"], 0.2)
        pipeline_module._WORKER_STATE.clear()


class TestAlignedImageReusesTheAngle(unittest.TestCase):
    """El repaso de firmas y el VLM heredan el enderezado del procesado.

    Ademas de ahorrar la deteccion, esto garantiza que el recorte medido
    contra el fondo del libro salga del mismo marco que vio el detector.
    """

    def setUp(self):
        self.pipeline = Pipeline(
            AppConfig(deskew=True, align=False),
            FakeEngine(),
            Template(name="fixture"),
        )

    def test_stored_angle_replaces_detection(self):
        with patch.object(pipeline_module, "deskew") as detect, \
                patch.object(pipeline_module, "rotate",
                             side_effect=lambda image, angle: image) as turn:
            self.pipeline._aligned_image(_page(), 0, None, skew_angle=-1.4)

        detect.assert_not_called()
        self.assertAlmostEqual(turn.call_args.args[1], -1.4)

    def test_without_a_stored_angle_it_detects(self):
        with patch.object(pipeline_module, "deskew",
                          return_value=(_page(), 0.0)) as detect:
            self.pipeline._aligned_image(_page(), 0, None)

        detect.assert_called_once()


if __name__ == "__main__":
    unittest.main()

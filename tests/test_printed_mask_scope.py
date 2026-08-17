"""El mapa de fondo impreso: solo si alguien lo lee, y sin retener el libro.

Dos propiedades:

1. Solo se construye cuando existe un consumidor -casillas en la plantilla
   o ``date_slot_ocr``-. Sin ninguno de los dos costaba una imagen en grises
   por pagina retenida toda la calibracion (0,34 GB en un libro de 393) sin
   que nadie mirara el resultado.
2. Cuando si se construye, la ventana deslizante da exactamente la misma
   mascara que retener el libro entero: el ancla de la pagina i es la
   mediana de [i-7, i+7] y queda fija en cuanto se calibra la i+7.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from app.core import pipeline as pipeline_module
from app.core.config import AppConfig
from app.core.pipeline import Pipeline
from app.templates.schema import FieldTemplate, FieldType, Template
from app.vision.alignment import TransformResult, warp_with_transform


class FakeEngine:
    name = "fake"


def _checkbox_template() -> Template:
    return Template(name="con casillas", fields=[
        FieldTemplate(id="marca", type=FieldType.CHECKBOX,
                      x=0.1, y=0.1, w=0.2, h=0.2),
    ])


def _pages(count: int, height: int = 40, width: int = 50) -> list:
    """Paginas con un marco impreso comun y ruido propio de cada una."""
    rng = np.random.default_rng(3)
    made = []
    for index in range(count):
        page = rng.integers(200, 256, (height, width, 3), dtype=np.uint8)
        page[5:8, :] = 10          # linea impresa, igual en todas
        page[:, 12:15] = 10
        page[20 + index % 5, :] = 30   # escritura, distinta en cada pagina
        made.append(page)
    return made


class TestConsumerGate(unittest.TestCase):
    def _pipeline(self, template: Template, **config) -> Pipeline:
        return Pipeline(AppConfig(**config), FakeEngine(), template)

    def test_no_consumer_without_checkboxes_or_slot_ocr(self):
        pipeline = self._pipeline(Template(name="solo ocr"),
                                  date_slot_ocr=False)
        self.assertFalse(pipeline._printed_mask_has_consumer())

    def test_checkbox_field_is_a_consumer(self):
        pipeline = self._pipeline(_checkbox_template(), date_slot_ocr=False)
        self.assertTrue(pipeline._printed_mask_has_consumer())

    def test_slot_ocr_is_a_consumer(self):
        pipeline = self._pipeline(Template(name="solo ocr"),
                                  date_slot_ocr=True)
        self.assertTrue(pipeline._printed_mask_has_consumer())

    def test_shipped_template_has_no_consumer(self):
        """La plantilla real no declara casillas y la GUI apaga slot_ocr."""
        from app.templates.manager import TemplateManager

        template = TemplateManager().load(
            Path(__file__).resolve().parents[1]
            / "template" / "aircraft_log.json"
        )
        pipeline = self._pipeline(template, date_slot_ocr=False)
        self.assertFalse(pipeline._printed_mask_has_consumer())

    def test_without_consumer_nothing_is_accumulated(self):
        pipeline = self._pipeline(
            Template(name="solo ocr"),
            align=True, deskew=False, remove_printed=True, date_slot_ocr=False,
        )
        pages = _pages(12)
        with patch.object(pipeline_module, "render_page",
                          side_effect=lambda *a, **k: pages[0]), \
                patch.object(pipeline_module, "compute_similarity_transform",
                             return_value=TransformResult(reliable=True)), \
                patch.object(pipeline_module.cv2, "cvtColor") as convert:
            pipeline._calibrate_impl(Path("fixture.pdf"), 1, 12, pages[0])

        convert.assert_not_called()
        self.assertIsNone(pipeline._printed_mask)


class TestSlidingWindowMatchesFullBuffer(unittest.TestCase):
    """La ventana deslizante y el buffer completo dan la misma mascara."""

    def _reference_mask(self, pages, reference, config, transforms):
        """El algoritmo anterior: retener todas las paginas y luego acumular."""
        calib_dpi = max(75, config.dpi // 2)
        zoom = calib_dpi / config.dpi
        calib_ref = cv2.resize(reference, None, fx=zoom, fy=zoom,
                               interpolation=cv2.INTER_AREA)
        factor = config.dpi / calib_dpi
        anchors = Pipeline._stabilize_anchors(transforms)
        accum = None
        aligned = 0
        for page, anchor in zip(pages, anchors):
            if not anchor.reliable:
                continue
            calib_tr = TransformResult(
                rot=anchor.rot, scale=anchor.scale,
                tx=anchor.tx / factor, ty=anchor.ty / factor,
            )
            warped = warp_with_transform(
                cv2.cvtColor(page, cv2.COLOR_BGR2GRAY), calib_tr,
                (calib_ref.shape[1], calib_ref.shape[0]),
            )
            dark = warped < config.printed_ink_threshold
            if accum is None:
                accum = np.zeros_like(dark, dtype=np.float32)
            accum += dark.astype(np.float32)
            aligned += 1
        if accum is None or not aligned:
            return None
        printed = (accum / aligned) >= 0.60
        printed = cv2.dilate(printed.astype(np.uint8), np.ones((3, 3), np.uint8))
        return cv2.resize(
            printed, (reference.shape[1], reference.shape[0]),
            interpolation=cv2.INTER_NEAREST,
        ).astype(bool)

    def _run(self, count: int, unreliable: set[int]) -> None:
        config = AppConfig(align=True, deskew=False, remove_printed=True,
                           date_slot_ocr=False, dpi=200)
        pages = _pages(count)
        transforms = [
            TransformResult(rot=0.0, tx=float(i % 3), ty=0.0, scale=1.0,
                            inliers=40, reliable=i not in unreliable)
            for i in range(count)
        ]
        # El pipeline escala tx/ty por ``factor`` al guardarlos, asi que la
        # referencia recibe los mismos numeros que produce la calibracion.
        scaled = [
            TransformResult(rot=t.rot, tx=t.tx * 2, ty=t.ty * 2,
                            scale=t.scale, inliers=t.inliers,
                            reliable=t.reliable)
            for t in transforms
        ]
        expected = self._reference_mask(pages, pages[0], config, scaled)

        pipeline = Pipeline(config, FakeEngine(), _checkbox_template())
        rendered = iter(pages)
        estimated = iter(transforms)
        with patch.object(pipeline_module, "render_page",
                          side_effect=lambda *a, **k: next(rendered)), \
                patch.object(pipeline_module, "compute_similarity_transform",
                             side_effect=lambda *a, **k: next(estimated)):
            pipeline._calibrate_impl(Path("fixture.pdf"), 1, count, pages[0])

        if expected is None:
            self.assertIsNone(pipeline._printed_mask)
            return
        self.assertIsNotNone(pipeline._printed_mask)
        self.assertTrue(np.array_equal(pipeline._printed_mask, expected))

    def test_short_book_below_the_window(self):
        self._run(5, unreliable=set())

    def test_book_longer_than_the_window(self):
        self._run(20, unreliable=set())

    def test_unreliable_pages_are_skipped_the_same_way(self):
        self._run(20, unreliable={0, 3, 11, 19})


if __name__ == "__main__":
    unittest.main()

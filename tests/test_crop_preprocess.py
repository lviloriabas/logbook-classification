"""Pruebas del interruptor de preprocesado de recortes.

Verifica que con ``preprocess=False`` los recortes llegan crudos al motor
(para comparar motores con escritura a mano) y que con ``True`` se
aplican el reescalado y la localización de tinta habituales.
"""

from __future__ import annotations

import numpy as np

from app.core.config import AppConfig
from app.core.pipeline import process_page_image
from app.models.schemas import OcrResult
from app.ocr import date_ocr as date_ocr_mod
from app.ocr.date_ocr import read_date_slots
from app.ocr.regional import ocr_regions
from app.templates.schema import FieldTemplate, Template
from app.vision.ink_extent import strip_date_label


class _FakeEngine:
    name = "fake"

    def __init__(self):
        self.seen: list = []

    def recognize(self, image, config=None):
        self.seen.append(image)
        return [OcrResult(text="X", confidence=0.9)] if image is not None else []

    def recognize_batch(self, images):
        self.seen.extend(images)
        return [[OcrResult(text="X", confidence=0.9)] for _ in images]


def _page(width=1000, height=1400, bgr=True):
    image = np.full((height, width, 3) if bgr else (height, width),
                    255, dtype=np.uint8)
    return image


def _field(**kwargs) -> FieldTemplate:
    base = dict(id="day", x=0.2, y=0.2, w=0.1, h=0.03)
    base.update(kwargs)
    return FieldTemplate(**base)


def test_ocr_regions_raw_crop_when_preprocess_off():
    engine = _FakeEngine()
    page = _page()
    field = _field(localize="ink")
    texts, confs = zip(*ocr_regions(engine, page, [field], 0.01,
                                    preprocess=False))
    assert texts == ("X",)
    sent = engine.seen[0]
    field_px = field.rect_pixels(page.shape[1], page.shape[0])
    pad_x = max(1, round(0.01 * (field_px[2] - field_px[0])))
    pad_y = max(1, round(0.01 * (field_px[3] - field_px[1])))
    assert sent.shape[:2] == (field_px[3] - field_px[1] + 2 * pad_y,
                              field_px[2] - field_px[0] + 2 * pad_x)


def test_ocr_regions_upscales_when_preprocess_on():
    engine = _FakeEngine()
    page = _page()
    field = _field(w=0.02, h=0.005)
    texts, _conf = zip(*ocr_regions(engine, page, [field], 0.01,
                                    preprocess=True))
    assert texts[0] == "X"
    best_side = max(engine.seen[0].shape[:2])
    # El recorte (22x9) se reescala con el tope de x3 para no manchar la
    # tinta: 22 -> ~66 (antes se forzaba a 800px exactos).
    assert best_side >= 60
    assert best_side < 800


def test_slots_raw_when_preprocess_off(monkeypatch):
    seen: list = []

    class FakeSlotEngine:  # noqa: N801
        name = "fake-slot"

        def recognize(self, image, config=None):
            seen.append((image.shape, config))
            return [OcrResult(text="2", confidence=0.9)]

    monkeypatch.setattr(date_ocr_mod, "_fallback_engine", lambda: FakeSlotEngine())
    slot = np.full((40, 60, 3), 255, dtype=np.uint8)
    text, conf = read_date_slots("day", "day", [slot],
                                          preprocess=False)
    assert text == "2"
    assert seen[0][0][:2] == (40, 60)
    assert "whitelist=0123456789" in seen[0][1]


def test_slots_upscaled_when_preprocess_on(monkeypatch):
    seen = []

    class FakeSlotEngine:  # noqa: N801
        name = "fake-slot"

        def recognize(self, image, config=None):
            seen.append(image.shape)
            return [OcrResult(text="2", confidence=0.9)]

    monkeypatch.setattr(date_ocr_mod, "_fallback_engine", lambda: FakeSlotEngine())
    slot = np.full((40, 60, 3), 255, dtype=np.uint8)
    read_date_slots("day", "day", [slot], preprocess=True)
    assert max(seen[0]) >= 60


def test_strip_date_label_preserves_lower_handwriting():
    region = np.zeros((40, 30, 3), dtype=np.uint8)
    result = strip_date_label(region)

    assert np.all(result[:6] == 255)
    assert np.all(result[10:] == 0)


def test_printed_mask_does_not_mutate_ocr_image():
    engine = _FakeEngine()
    page = _page(100, 100)
    page[30:50, 30:50] = 0
    field = FieldTemplate(id="value", x=0.2, y=0.2, w=0.4, h=0.4)
    template = Template(name="test", fields=[field])

    process_page_image(
        page, 1, AppConfig(dpi=150, deskew=False, align=False,
                           vlm_enabled=False), engine, template, None,
        printed_mask=np.ones((100, 100), dtype=bool),
    )

    assert engine.seen[0].min() == 0

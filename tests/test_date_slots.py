"""Pruebas de ranuras de casilla y decodificación por carácter.

Las casillas day/month/year van partidas por separadores verticales
impresos. Se validan la segmentación (app.vision.date_slots) y el
decodificador con restricciones (app.ocr.date_ocr.decode_slots) sin
ejecutar el OCR real.
"""

from __future__ import annotations

import unittest

import cv2
import numpy as np

from app.ocr.date_ocr import decode_slots
from app.templates.schema import FieldTemplate, Template
from app.vision.date_slots import (
    build_slot_maps,
    compute_slot_map,
    crop_slots,
)

PAGE_W, PAGE_H = 1000, 1200


def _day_field() -> FieldTemplate:
    return FieldTemplate(id="day", x=0.20, y=0.20, w=0.18, h=0.05,
                         postprocess="day")


def _month_field() -> FieldTemplate:
    return FieldTemplate(id="month", x=0.40, y=0.20, w=0.24, h=0.05,
                         postprocess="month")


def _year_field() -> FieldTemplate:
    return FieldTemplate(id="year", x=0.66, y=0.20, w=0.18, h=0.05,
                         postprocess="year")


def _template() -> Template:
    return Template(name="test", fields=[
        _day_field(), _month_field(), _year_field(),
    ])


def _draw_vline(mask: np.ndarray, x: int, y0: int, y1: int) -> None:
    mask[y0:y1, x - 2:x + 2] = True


def _draw_label(mask: np.ndarray, x: int, y_baseline: int,
                text: str = "DAY") -> None:
    """Rótulo impreso pequeño dentro de la banda (bajo <SEP_MIN_RATIO)."""
    canvas = np.zeros((PAGE_H, PAGE_W), np.uint8)
    cv2.putText(canvas, text, (x, y_baseline), cv2.FONT_HERSHEY_SIMPLEX,
                0.35, 255, 1, cv2.LINE_AA)
    mask |= canvas > 0


_BAND = {}  # rect pixeles de cada campo, para los helpers de test


def _band(field: FieldTemplate):
    key = field.id
    if key not in _BAND:
        _BAND[key] = field.rect_pixels(PAGE_W, PAGE_H)
    return _BAND[key]


class TestComputeSlotMap(unittest.TestCase):
    def test_detects_vertical_separators(self):
        field = _day_field()
        left, top, right, bottom = _band(field)
        sep = (left + right) // 2
        mask = np.zeros((PAGE_H, PAGE_W), bool)
        _draw_vline(mask, sep, top + 1, bottom - 1)
        spec = compute_slot_map(mask, field)
        self.assertIsNotNone(spec)
        self.assertEqual(spec["slots"], 2)
        self.assertTrue(abs(spec["boundaries"][1] - sep) <= 2)

    def test_label_is_not_a_separator(self):
        field = _day_field()
        left, top, right, bottom = _band(field)
        mask = np.zeros((PAGE_H, PAGE_W), bool)
        _draw_label(mask, left + 6, bottom - 8)
        _draw_vline(mask, (left + right) // 2, top + 1, bottom - 1)
        spec = compute_slot_map(mask, field)
        self.assertIsNotNone(spec)
        self.assertEqual(spec["slots"], 2)
        self.assertEqual(len(spec["boundaries"]), 3)

    def test_no_separators_returns_none(self):
        field = _day_field()
        left, top, right, bottom = _band(field)
        mask = np.zeros((PAGE_H, PAGE_W), bool)
        _draw_label(mask, left + 6, bottom - 6)
        self.assertIsNone(compute_slot_map(mask, field))

    def test_build_slot_maps_includes_date_fields(self):
        mask = np.zeros((PAGE_H, PAGE_W), bool)
        for field, index in ((_day_field(), 1), (_month_field(), 1),
                             (_year_field(), 1)):
            left, top, right, bottom = field.rect_pixels(PAGE_W, PAGE_H)
            sep = left + (right - left) * index // 2
            _draw_vline(mask, sep, top + 1, bottom - 1)
        maps = build_slot_maps(mask, _template())
        self.assertEqual(set(maps), {"day", "month", "year"})
        for spec in maps.values():
            self.assertEqual(spec["slots"], 2)
            self.assertEqual(len(spec["boundaries"]), 3)

    def test_build_slot_maps_uniform_fallback(self):
        mask = np.zeros((PAGE_H, PAGE_W), bool)
        maps = build_slot_maps(mask, _template())
        for spec in maps.values():
            self.assertTrue(len(spec["boundaries"]) == spec["slots"] + 1)


class TestCropSlots(unittest.TestCase):
    def test_slots_split_along_boundaries(self):
        field = _day_field()
        left, top, right, bottom = field.rect_pixels(PAGE_W, PAGE_H)
        sep = (left + right) // 2
        mask = np.zeros((PAGE_H, PAGE_W), bool)
        _draw_vline(mask, sep, top + 1, bottom - 1)
        spec = compute_slot_map(mask, field)

        page = np.full((PAGE_H, PAGE_W, 3), 255, np.uint8)
        slots = crop_slots(page, field, spec=spec)
        self.assertIsNotNone(slots)
        self.assertEqual(len(slots), 2)
        slot_width = (right - left) // 2
        for slot in slots:
            self.assertTrue(slot_width * 0.6 <= slot.shape[1] <= slot_width * 1.4)

    def test_crop_slots_outside_padding(self):
        # Un límite fuera del ancho recortado (más allá del borde + pad) se
        # descarta por intersección vacía; los que tocan el borde se usan.
        field = _day_field()
        page = np.full((PAGE_H, PAGE_W, 3), 255, np.uint8)
        spec = {"boundaries": [200, 300, 380], "slots": 2}
        slots = crop_slots(page, field, 0.02, spec)
        self.assertIsNotNone(slots)
        self.assertEqual(len(slots), 2)


class TestDecodeSlots(unittest.TestCase):
    def test_day_two_slots(self):
        self.assertEqual(decode_slots("day", [("2", 0.9), ("0", 0.8)]),
                         ("20", 0.85))

    def test_day_single_slot(self):
        self.assertEqual(decode_slots("day", [("7", 0.7), ("", 0.0)]),
                         ("7", 0.7))

    def test_year_four_slots(self):
        self.assertEqual(decode_slots("year", [("2", 0.9), ("0", 0.9),
                                               ("2", 0.8), ("6", 0.9)]),
                         ("2026", 0.875))

    def test_empty_readings_rejected(self):
        text, conf = decode_slots("month", [("", 0.0), ("", 0.0), ("", 0.0)])
        self.assertEqual((text, conf), ("", 0.0))

    def test_month_exact(self):
        text, conf = decode_slots("month", [("J", 0.9), ("U", 0.8),
                                            ("L", 0.7)])
        self.assertEqual(text, "JUL")
        self.assertAlmostEqual(conf, 0.8)

    def test_month_with_empty_slot(self):
        # Con una ranura vacía, JUN y JUL empatan (ambigüedad intrínseca);
        # el decodificador devuelve cualquiera de los dos, nunca basura.
        text, _ = decode_slots("month", [("J", 0.9), ("U", 0.8), ("", 0.0)])
        self.assertIn(text, {"JUN", "JUL"})

    def test_month_misread_letter(self):
        # 'GUL' (G = confusión típica de J) se resuelve a JUL por restricción.
        text, _ = decode_slots("month", [("G", 0.7), ("U", 0.8), ("L", 0.7)])
        self.assertEqual(text, "JUL")

    def test_month_english_word(self):
        text, _ = decode_slots("month", [("D", 0.9), ("E", 0.8), ("C", 0.9)])
        self.assertEqual(text, "DEC")


if __name__ == "__main__":
    unittest.main()
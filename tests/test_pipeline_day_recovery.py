"""Regresiones del OCR estructurado para la casilla de dia."""

from __future__ import annotations

from unittest.mock import patch

import numpy as np

from app.core import pipeline as pipeline_module
from app.core.config import AppConfig
from app.core.pipeline import process_page_image
from app.models.schemas import PageResult
from app.templates.schema import FieldTemplate, Template


def _day_template() -> Template:
    return Template(
        name="fixture",
        fields=[FieldTemplate(
            id="day",
            required=True,
            x=0.2,
            y=0.2,
            w=0.2,
            h=0.1,
            postprocess="day",
        )],
    )


def _page() -> np.ndarray:
    image = np.full((100, 120, 3), 255, dtype=np.uint8)
    image[0:20, 0:20] = 0
    return image


def _day_result(page: PageResult):
    return next(field for field in page.fields if field.field_id == "day")


def test_single_digit_day_uses_slot_recovery():
    config = AppConfig(
        dpi=200,
        date_dpi=200,
        deskew=False,
        align=False,
        date_slot_ocr=True,
    )
    slot_map = {"day": {"boundaries": [24, 36, 48], "slots": 2}}

    with patch.object(
        pipeline_module, "ocr_regions", return_value=[("7", 0.9)]
    ), patch.object(
        pipeline_module, "_slot_ocr_fallback", return_value=("20", 0.8)
    ) as slot_reader:
        page = process_page_image(
            _page(), 1, config, object(), _day_template(), None,
            slot_map=slot_map,
        )

    result = _day_result(page)
    assert result.value == "20"
    assert result.source == "ocr_fallback"
    assert result.inference_method == "ranuras"
    slot_reader.assert_called_once()


def test_clean_two_digit_day_is_still_checked_by_slots():
    config = AppConfig(
        dpi=200,
        date_dpi=200,
        deskew=False,
        align=False,
        date_slot_ocr=True,
    )
    slot_map = {"day": {"boundaries": [24, 36, 48], "slots": 2}}

    with patch.object(
        pipeline_module, "ocr_regions", return_value=[("20", 0.9)]
    ), patch.object(
        pipeline_module, "_slot_ocr_fallback", return_value=("21", 0.95)
    ) as slot_reader:
        page = process_page_image(
            _page(), 1, config, object(), _day_template(), None,
            slot_map=slot_map,
        )

    result = _day_result(page)
    assert result.value == "21"
    assert result.inference_method == "ranuras"
    assert result.alternatives == ["20"]
    slot_reader.assert_called_once()


def test_high_resolution_date_image_receives_scaled_alignment():
    config = AppConfig(
        dpi=200,
        date_dpi=400,
        deskew=False,
        align=True,
        date_slot_ocr=False,
    )
    transform = pipeline_module.TransformResult(
        tx=3, ty=-2, reliable=True
    )
    seen = []

    def fake_apply(image, current):
        seen.append((image.shape, current.tx, current.ty))
        return image

    with patch.object(
        pipeline_module, "ocr_regions", return_value=[("20", 0.9)]
    ), patch.object(pipeline_module, "apply_transform", side_effect=fake_apply):
        page = process_page_image(
            _page(), 1, config, object(), _day_template(),
            np.zeros((100, 120, 3), dtype=np.uint8),
            transform=transform,
            transform_reliable=True,
            date_image=np.zeros((200, 240, 3), dtype=np.uint8),
        )

    assert _day_result(page).value == "20"
    assert seen == [
        ((100, 120, 3), 3, -2),
        ((200, 240, 3), 6, -4),
    ]

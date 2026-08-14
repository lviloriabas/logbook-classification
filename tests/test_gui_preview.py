"""Geometría usada por los campos del visor principal."""

from __future__ import annotations

import pickle

import numpy as np

from app.core.config import AppConfig
from app.core.pipeline import process_page_image
from app.gui.main_window import _visible_preview_fields
from app.models.schemas import PageResult
from app.templates.schema import FieldTemplate, Template
from app.vision.alignment import TransformResult


class EmptyEngine:
    name = "empty"

    def recognize_batch(self, images):
        return [[] for _image in images]


def _template() -> Template:
    return Template(
        name="preview",
        fields=[
            FieldTemplate(
                id="log_number",
                x=0.10,
                y=0.10,
                w=0.20,
                h=0.10,
                required=True,
                postprocess="digits",
            ),
            FieldTemplate(
                id="day_1",
                x=0.40,
                y=0.10,
                w=0.10,
                h=0.10,
                required=False,
                postprocess="char",
            ),
        ],
    )


def test_important_preview_fields_are_required_fields():
    template = _template()

    assert [f.id for f in _visible_preview_fields(template, False)] == [
        "log_number",
        "day_1",
    ]
    assert [f.id for f in _visible_preview_fields(template, True)] == [
        "log_number"
    ]


def test_pipeline_records_effective_preview_geometry_without_serializing_it():
    image = np.full((100, 200, 3), 255, dtype=np.uint8)
    transform = TransformResult(rot=0.2, tx=10.0, ty=5.0, scale=1.001)
    page = process_page_image(
        image,
        1,
        AppConfig(
            blank_threshold=0.0,
            deskew=False,
            align=True,
            date_dynamic_geometry=False,
            date_slot_ocr=False,
            date_ocr_fallback=False,
            crop_preprocess=False,
        ),
        EmptyEngine(),
        _template(),
        image,
        transform=transform,
        transform_reliable=True,
    )

    assert page.preview_alignment == {
        "rot": 0.2,
        "tx_ratio": 0.05,
        "ty_ratio": 0.05,
        "scale": 1.001,
    }
    assert page.preview_boxes["log_number"] == [0.10, 0.10, 0.20, 0.10]
    restored = pickle.loads(pickle.dumps(page))
    assert restored.preview_alignment == page.preview_alignment
    assert restored.preview_boxes == page.preview_boxes
    dumped = page.model_dump(mode="json")
    assert "preview_alignment" not in dumped
    assert "preview_boxes" not in dumped


def test_preview_metadata_defaults_do_not_change_page_report_shape():
    dumped = PageResult(page_number=1).model_dump(mode="json")

    assert "preview_alignment" not in dumped
    assert "preview_boxes" not in dumped

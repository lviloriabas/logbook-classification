"""Pruebas de propagacion del mapa de ranuras en ambos modos."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from app.core import pipeline as pipeline_module
from app.core.config import AppConfig
from app.core.pipeline import Pipeline
from app.models.schemas import PageResult
from app.templates.schema import Template
from app.vision.alignment import TransformResult


class FakeEngine:
    name = "fake"


class TestSlotMapPropagation(unittest.TestCase):
    def setUp(self):
        self.template = Template(name="fixture")
        self.pipeline = Pipeline(
            AppConfig(align=False, date_slot_ocr=True),
            FakeEngine(),
            self.template,
        )
        self.pipeline._date_slot_map = {
            "day": {"boundaries": [0, 10, 20], "slots": 2}
        }

    def test_sequential_passes_slot_map(self):
        captured = {}

        def fake_process(*args, **kwargs):
            captured.update(kwargs)
            return PageResult(page_number=1)

        with patch.object(
            pipeline_module, "render_page",
            return_value=np.full((20, 20, 3), 255, np.uint8),
        ), patch.object(pipeline_module, "process_page_image", fake_process):
            self.pipeline._process_sequential(
                Path("fixture.pdf"), 1, 1, np.zeros((20, 20, 3), np.uint8)
            )

        self.assertIs(captured["slot_map"], self.pipeline._date_slot_map)

    def test_worker_passes_slot_map(self):
        pipeline_module._WORKER_STATE.clear()
        pipeline_module._WORKER_STATE.update({
            "pdf_path": Path("fixture.pdf"),
            "config": AppConfig(align=False),
            "engine": FakeEngine(),
            "template": self.template,
            "reference": None,
            "transforms": [TransformResult(reliable=False)],
            "own_reliability": [False],
            "printed_mask": None,
            "slot_map": self.pipeline._date_slot_map,
        })
        captured = {}

        def fake_process(*args, **kwargs):
            captured.update(kwargs)
            return PageResult(page_number=1)

        with patch.object(pipeline_module, "process_page", fake_process):
            pipeline_module._process_page_worker(1)

        self.assertIs(captured["slot_map"], self.pipeline._date_slot_map)


if __name__ == "__main__":
    unittest.main()

"""Pruebas de la puerta de seguridad de alineacion."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import cv2
import numpy as np

from app.core.config import AppConfig
from app.core.pipeline import Pipeline
from app.vision import alignment as alignment_module
from app.vision.alignment import (
    TransformResult,
    align_to_template,
    compute_similarity_transform,
    load_template_reference,
    printed_structure,
    scale_transform_for_shape,
)
from app.templates.manager import TemplateManager
from app.templates.schema import Template


class TestAlignmentGate(unittest.TestCase):
    def test_unreliable_transform_does_not_warp_image(self):
        image = np.full((40, 50, 3), 255, np.uint8)
        template = image.copy()
        transform = TransformResult(tx=12, ty=-4, reliable=False)
        with patch(
            "app.vision.alignment.compute_similarity_transform",
            return_value=transform,
        ):
            aligned, quality = align_to_template(
                image, template, AppConfig(align=True)
            )

        self.assertEqual(quality, "low")
        np.testing.assert_array_equal(aligned, image)

    def test_reliable_transform_uses_reference_canvas_size(self):
        page = np.full((40, 50, 3), 255, np.uint8)
        template = np.full((45, 60, 3), 255, np.uint8)
        transform = TransformResult(reliable=True)
        with patch(
            "app.vision.alignment.compute_similarity_transform",
            return_value=transform,
        ):
            aligned, quality = align_to_template(
                page, template, AppConfig(align=True)
            )

        self.assertEqual(quality, "ok")
        self.assertEqual(aligned.shape, template.shape)

    def test_anchor_is_unreliable_without_reliable_window_member(self):
        transforms = [
            TransformResult(tx=5, reliable=False),
            TransformResult(tx=8, reliable=False),
        ]
        anchors = Pipeline._stabilize_anchors(transforms)
        self.assertEqual(len(anchors), 2)
        self.assertTrue(all(not anchor.reliable for anchor in anchors))

    def test_anchor_uses_reliable_members_only(self):
        transforms = [
            TransformResult(tx=5, reliable=False),
            TransformResult(tx=8, reliable=True),
            TransformResult(tx=100, reliable=False),
        ]
        anchors = Pipeline._stabilize_anchors(transforms)
        self.assertTrue(all(anchor.reliable for anchor in anchors))
        self.assertTrue(all(anchor.tx == 8 for anchor in anchors))

    def test_transform_translation_scales_with_render_resolution(self):
        transform = TransformResult(tx=5, ty=-3, scale=1.002,
                                    inliers=40, reliable=True)

        scaled = scale_transform_for_shape(
            transform, (100, 200, 3), (300, 600, 3)
        )

        self.assertEqual(scaled.tx, 15)
        self.assertEqual(scaled.ty, -9)
        self.assertEqual(scaled.rot, transform.rot)
        self.assertEqual(scaled.scale, transform.scale)
        self.assertTrue(scaled.reliable)

    def test_phase_fallback_recovers_translation_when_features_fail(self):
        template = np.zeros((128, 128), dtype=np.uint8)
        template[35:80, 25:90] = 180
        page = np.zeros_like(template)
        page[35:80, 31:96] = 180

        with patch.object(
            alignment_module, "_feature_transform", return_value=None
        ):
            transform = compute_similarity_transform(
                page, template, AppConfig(min_match_count=10)
            )

        self.assertEqual(transform.method, "phase")
        self.assertTrue(transform.reliable)
        self.assertAlmostEqual(transform.tx, -6.0, delta=0.5)
        self.assertAlmostEqual(transform.ty, 0.0, delta=0.5)

    def test_separator_without_printed_grid_is_rejected(self):
        separator = np.full((400, 600, 3), 255, dtype=np.uint8)
        cv2.putText(
            separator, "SEPARADOR", (120, 210), cv2.FONT_HERSHEY_SIMPLEX,
            2.0, (0, 0, 0), 4,
        )
        form = np.full_like(separator, 255)
        for x in range(30, 571, 60):
            cv2.line(form, (x, 20), (x, 380), (0, 0, 0), 2)
        for y in range(20, 381, 45):
            cv2.line(form, (30, y), (570, y), (0, 0, 0), 2)

        transform = compute_similarity_transform(
            separator, form, AppConfig(min_match_count=10)
        )

        self.assertFalse(transform.reliable)

    def test_printed_structure_discards_short_handwriting(self):
        page = np.full((300, 500, 3), 255, dtype=np.uint8)
        cv2.line(page, (20, 80), (480, 80), (0, 0, 0), 2)
        cv2.line(page, (100, 20), (100, 280), (0, 0, 0), 2)
        cv2.putText(
            page, "ABC", (210, 180), cv2.FONT_HERSHEY_SCRIPT_SIMPLEX,
            1.0, (0, 0, 0), 2,
        )

        structure = printed_structure(page)

        self.assertGreater(np.count_nonzero(structure[77:84, 30:470]), 900)
        self.assertLess(np.count_nonzero(structure[145:190, 190:290]), 350)

    def test_reference_path_is_relative_to_template_file_and_scales(self):
        with TemporaryDirectory(dir=Path("tmp")) as directory:
            root = Path(directory)
            reference = np.full((30, 40, 3), 173, dtype=np.uint8)
            cv2.imwrite(str(root / "reference.png"), reference)
            template_path = root / "template.json"
            template_path.write_text(
                '{"name":"fixture","reference_image":"reference.png",'
                '"reference_dpi":150,"fields":[]}',
                encoding="utf-8",
            )
            template = TemplateManager(root).load(template_path)

            loaded = load_template_reference(template, 300)

        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.shape[:2], (60, 80))

    def test_reference_metadata_is_not_serialized_as_runtime_source_path(self):
        template = Template(
            name="fixture",
            reference_image="reference.png",
            source_path=Path("template.json"),
        )

        dumped = template.model_dump(mode="json")

        self.assertEqual(dumped["reference_image"], "reference.png")
        self.assertNotIn("source_path", dumped)


if __name__ == "__main__":
    unittest.main()

"""Pruebas de la puerta de seguridad de alineacion."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np

from app.core.config import AppConfig
from app.core.pipeline import Pipeline
from app.vision import alignment as alignment_module
from app.vision.alignment import (
    TransformResult,
    align_to_template,
    compute_similarity_transform,
    scale_transform_for_shape,
)


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


if __name__ == "__main__":
    unittest.main()

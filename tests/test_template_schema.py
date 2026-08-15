"""Validaciones de geometria y unicidad de las plantillas."""

from __future__ import annotations

import unittest

from pydantic import ValidationError

from app.templates.schema import FieldTemplate, FieldType, Template


class TestFieldGeometry(unittest.TestCase):
    def test_rejects_field_outside_right_edge(self):
        with self.assertRaises(ValidationError):
            FieldTemplate(id="bad", x=0.9, y=0.1, w=0.2, h=0.1)

    def test_rejects_field_outside_bottom_edge(self):
        with self.assertRaises(ValidationError):
            FieldTemplate(id="bad", x=0.1, y=0.9, w=0.1, h=0.2)

    def test_rejects_inverted_signature_thresholds(self):
        """El umbral de campo vacío no puede exigir más tinta que el de
        firma presente: dejaría la franja incierta al revés."""
        with self.assertRaises(ValidationError):
            FieldTemplate(
                id="bad",
                type=FieldType.SIGNATURE,
                x=0.1,
                y=0.1,
                w=0.2,
                h=0.1,
                min_ink_peak=0.05,
                max_empty_peak=0.20,
            )


class TestTemplateIds(unittest.TestCase):
    def test_rejects_duplicate_field_ids(self):
        fields = [
            FieldTemplate(id="same", x=0.1, y=0.1, w=0.1, h=0.1),
            FieldTemplate(id="same", x=0.3, y=0.1, w=0.1, h=0.1),
        ]
        with self.assertRaises(ValidationError):
            Template(name="bad", fields=fields)


if __name__ == "__main__":
    unittest.main()

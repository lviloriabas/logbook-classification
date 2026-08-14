"""Localización robusta de la retícula manuscrita DD|MMM|AA."""

from __future__ import annotations

import cv2
import numpy as np

from app.core.pipeline import _date_geometries_from_region
from app.templates.schema import FieldTemplate, Template
from app.vision.date_geometry import locate_date_grid
from app.vision.pdf_loader import RenderedRegion


def _template() -> Template:
    return Template(
        name="date-grid",
        fields=[
            FieldTemplate(id="day", x=.20, y=.20, w=.12, h=.12),
            FieldTemplate(id="month", x=.40, y=.20, w=.18, h=.12),
            FieldTemplate(id="year", x=.68, y=.20, w=.12, h=.12),
        ],
    )


def _page(missing: set[int] = frozenset()) -> np.ndarray:
    page = np.full((400, 600, 3), 235, np.uint8)
    # Retícula desplazada (+10, -7) con respecto a la plantilla.
    positions = [130, 166, 202, 250, 286, 322, 358, 418, 454, 490]
    top, bottom = 73, 121
    cv2.line(page, (110, top), (510, top), (45, 45, 45), 2)
    cv2.line(page, (110, bottom), (510, bottom), (45, 45, 45), 2)
    for index, x in enumerate(positions):
        if index not in missing:
            cv2.line(page, (x, top), (x, bottom), (55, 55, 55), 2)
    # Sombras y trazos manuscritos no verticales.
    page[:, 375:386] = 170
    cv2.putText(page, "JUL", (250, 112), cv2.FONT_HERSHEY_SIMPLEX,
                1.0, (20, 20, 20), 2, cv2.LINE_AA)
    return page


def test_grid_is_found_when_some_printed_lines_are_missing():
    geometry = locate_date_grid(_page({1, 7}), _template())

    assert set(geometry) == {"day", "month", "year"}
    assert all(item.score >= 0.6 for item in geometry.values())
    assert geometry["day"].rect[0] == 130
    assert geometry["year"].rect[2] == 490


def test_grid_rejects_page_without_a_distributed_comb():
    page = np.full((400, 600, 3), 235, np.uint8)
    for x in (130, 150, 170, 190, 210, 230):
        cv2.line(page, (x, 70), (x, 125), (40, 40, 40), 2)

    assert locate_date_grid(page, _template()) == {}


def test_grid_does_not_jump_to_a_neighbouring_table():
    page = _page()
    shifted = np.full_like(page, 235)
    # Un peine completo, pero demasiado lejos de la posición de la fecha.
    shifted[:, 40:] = page[:, :-40]

    assert locate_date_grid(shifted, _template()) == {}


def test_regional_grid_matches_full_page_coordinates():
    page = _page({1, 7})
    rect = (0.10, 0.10, 0.80, 0.30)
    height, width = page.shape[:2]
    x0, y0 = round(rect[0] * width), round(rect[1] * height)
    x1 = round((rect[0] + rect[2]) * width)
    y1 = round((rect[1] + rect[3]) * height)
    region = RenderedRegion(page[y0:y1, x0:x1], rect, dpi=200)
    template = _template()
    local_template = template.model_copy(update={
        "fields": [region.local_field(field) for field in template.fields]
    })

    local = locate_date_grid(
        region.image,
        local_template,
        coordinate_shape=page.shape[:2],
    )
    projected = _date_geometries_from_region(local, region, page.shape)
    full = locate_date_grid(page, template)

    assert set(projected) == set(full) == {"day", "month", "year"}
    for field_id in full:
        assert np.max(np.abs(
            np.array(projected[field_id].rect) - np.array(full[field_id].rect)
        )) <= 1

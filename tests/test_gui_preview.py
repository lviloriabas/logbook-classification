"""Geometría usada por los campos del visor principal."""

from __future__ import annotations

import os
import pickle
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtWidgets import QApplication, QPushButton

from app.core.config import AppConfig
from app.core.pipeline import process_page_image
from app.gui.main_window import MainWindow, _visible_preview_fields
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


def test_main_preview_uses_one_editable_global_page_and_tracks_file(
    tmp_path: Path, monkeypatch
):
    from app.vision import pdf_loader

    first = tmp_path / "book-a.pdf"
    second = tmp_path / "book-b.pdf"
    first.touch()
    second.touch()
    totals = {first.name: 2, second.name: 3}
    monkeypatch.setattr(
        pdf_loader, "page_count", lambda path: totals[Path(path).name]
    )

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        window.show()
        app.processEvents()
        window._set_preview_documents([first, second])
        window._preview_pdf = first
        window._preview_page = 2
        window._preview_total = 2
        window._update_preview_nav()

        assert not hasattr(window, "preview_pdf_combo")
        assert window.page_edit.width() == 48
        assert window.page_edit.text() == "2"
        assert window.page_total_label.text() == "de 5"
        assert window.preview_file_label.text() == "book-a.pdf"
        pagination_center = window.preview_pagination.mapToGlobal(
            window.preview_pagination.rect().center()
        ).x()
        pdf_center = window.preview_scroll.mapToGlobal(
            window.preview_scroll.rect().center()
        ).x()
        assert abs(pagination_center - pdf_center) <= 1
        file_center = window.preview_file_indicator.mapToGlobal(
            window.preview_file_indicator.rect().center()
        )
        pagination_global_center = window.preview_pagination.mapToGlobal(
            window.preview_pagination.rect().center()
        )
        assert file_center.x() < pagination_global_center.x()
        assert abs(file_center.y() - pagination_global_center.y()) <= 1
        window.preview_file_label.setText(
            "bitacora-con-un-nombre-muy-largo-que-no-debe-tapar-el-paginador.pdf"
        )
        app.processEvents()
        file_rect = window.preview_file_indicator.rect()
        file_right = window.preview_file_indicator.mapToGlobal(
            file_rect.topRight()
        ).x()
        pagination_left = window.preview_pagination.mapToGlobal(
            window.preview_pagination.rect().topLeft()
        ).x()
        assert file_right < pagination_left
        assert "Ir" not in {
            button.text() for button in window.findChildren(QPushButton)
        }

        window._next_page()
        assert window._preview_pdf == second
        assert window._preview_page == 1
        assert window.page_edit.text() == "3"
        assert window.preview_file_label.text() == "book-b.pdf"
        assert "Página 1 de 3" in window.preview_context_label.text()

        window.page_edit.setText("5")
        window.page_edit.editingFinished.emit()
        assert window._preview_pdf == second
        assert window._preview_page == 3
        assert window.page_edit.text() == "5"
    finally:
        window.close()
        app.processEvents()

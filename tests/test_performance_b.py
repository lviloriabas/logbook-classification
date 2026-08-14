"""Regresiones de las optimizaciones del perfil de rendimiento B."""

from __future__ import annotations

from unittest.mock import patch

import cv2
import numpy as np
import pymupdf as fitz

from app.core.config import AppConfig, config_for_pdf
from app.core.pipeline import OcrProcessPool, Pipeline
from app.models.schemas import OcrResult
from app.ocr.engine import TesseractOcrEngine
from app.templates.schema import Template
from app.vision.alignment import TransformResult, apply_transform
from app.vision.pdf_loader import PdfPageRenderer
from app.vision.preprocessing import rotate


def _fixture_pdf(path) -> None:
    document = fitz.open()
    page = document.new_page(width=220, height=300)
    page.draw_rect(fitz.Rect(20, 20, 200, 280), color=(0, 0, 0))
    page.insert_text((50, 80), "12 JUL 26", fontsize=18)
    document.save(path)
    document.close()


def test_config_for_pdf_caps_base_and_preserves_native_date_detail(tmp_path):
    pdf = tmp_path / "fixture.pdf"
    pdf.touch()
    config = AppConfig(dpi=200, date_dpi=600)

    with patch("app.vision.pdf_loader.detect_dpi", return_value=300):
        effective = config_for_pdf(config, pdf)
    assert (effective.dpi, effective.date_dpi) == (200, 300)

    with patch("app.vision.pdf_loader.detect_dpi", return_value=150):
        effective = config_for_pdf(config, pdf)
    assert (effective.dpi, effective.date_dpi) == (150, 150)


def test_high_resolution_region_matches_full_page_transform(tmp_path):
    pdf = tmp_path / "fixture.pdf"
    _fixture_pdf(pdf)
    rect = (0.35, 0.10, 0.55, 0.25)
    transform = TransformResult(
        rot=0.2, tx=3.0, ty=-2.0, scale=1.002, reliable=True
    )
    deskew_angle = 0.3

    with PdfPageRenderer(pdf) as renderer:
        base = renderer.render_page(1, 100)
        full = renderer.render_page(1, 300)
        region = renderer.render_aligned_region(
            1, 300, rect, base.shape, deskew_angle, transform
        )

    full = rotate(full, deskew_angle)
    scaled = TransformResult(
        rot=transform.rot,
        tx=transform.tx * full.shape[1] / base.shape[1],
        ty=transform.ty * full.shape[0] / base.shape[0],
        scale=transform.scale,
        reliable=True,
    )
    full = apply_transform(full, scaled)
    x, y, width, height = region.rect
    full_height, full_width = full.shape[:2]
    expected = full[
        round(y * full_height):round((y + height) * full_height),
        round(x * full_width):round((x + width) * full_width),
    ]

    assert region.image.shape == expected.shape
    difference = cv2.absdiff(region.image, expected)
    assert float(difference.mean()) < 1.0
    assert region.image.nbytes < full.nbytes * 0.25


def test_tesseract_batch_uses_one_imagelist_and_keeps_config():
    engine = TesseractOcrEngine(lang="eng", tesseract_cmd="tesseract.exe")
    images = [
        np.full((30, 40, 3), 255, np.uint8),
        np.full((30, 40, 3), 255, np.uint8),
    ]
    calls = []

    def fake_image_to_data(source, **kwargs):
        calls.append((source, kwargs["config"]))
        return {
            "page_num": [1, 2],
            "text": ["2", "7"],
            "conf": ["91", "87"],
        }

    config = "--psm 10 -c tessedit_char_whitelist=0123456789"
    with patch("pytesseract.image_to_data", side_effect=fake_image_to_data):
        results = engine.recognize_batch(images, config=config)

    assert len(calls) == 1
    assert calls[0][1] == config
    assert results == [
        [OcrResult(text="2", confidence=0.91)],
        [OcrResult(text="7", confidence=0.87)],
    ]


def test_ocr_process_pool_is_reused_across_pdfs(tmp_path):
    pdfs = [tmp_path / "first.pdf", tmp_path / "second.pdf"]
    for pdf in pdfs:
        _fixture_pdf(pdf)
    config = AppConfig(
        dpi=100,
        date_dpi=100,
        blank_threshold=0,
        deskew=False,
        align=False,
        remove_printed=False,
        date_ocr_fallback=False,
        date_slot_ocr=False,
    )
    engine = TesseractOcrEngine(lang="eng", tesseract_cmd="tesseract.exe")
    template = Template(name="empty")

    with OcrProcessPool(2, config, "tesseract", "eng", 1) as process_pool:
        reports = [
            Pipeline(
                config,
                engine,
                template,
                workers=2,
                cpu_threads=1,
                process_pool=process_pool,
            ).process(pdf)
            for pdf in pdfs
        ]

    assert [len(report.pages) for report in reports] == [1, 1]
    assert all(report.pages[0].processing_ms >= 0 for report in reports)

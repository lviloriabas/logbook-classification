"""El rango del lote recorta de verdad lo que procesan Pipeline y lote."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import numpy as np
import pymupdf as fitz

from app.core.config import AppConfig
from app.core.page_range import PageRange
from app.core.pipeline import Pipeline, process_pdf_batch
from app.models.schemas import PageResult
from app.templates.schema import Template


class _FakeEngine:
    name = "fake"


def _pdf(path: Path, pages: int) -> Path:
    """PDF de ``pages`` páginas, cada una rotulada con su número."""
    document = fitz.open()
    for number in range(1, pages + 1):
        page = document.new_page(width=200, height=280)
        page.insert_text((40, 60), f"PAGINA {number}", fontsize=16)
    document.save(path)
    document.close()
    return path


def _config() -> AppConfig:
    return AppConfig(dpi=72, date_dpi=72, deskew=False, align=False,
                     remove_printed=False, date_ocr_fallback=False,
                     date_slot_ocr=False, vlm_enabled=False)


def _pipeline(**kwargs) -> Pipeline:
    return Pipeline(_config(), _FakeEngine(), Template(name="empty"), **kwargs)


def _pages_processed(report) -> list[int]:
    return [page.page_number for page in report.pages]


def test_a_range_processes_only_its_pages_and_keeps_their_real_numbers(tmp_path):
    """El número de página del reporte es el del PDF, no el del tramo."""
    pdf = _pdf(tmp_path / "book.pdf", 12)
    seen: list[int] = []

    def fake_process(_image, page_number, *_args, **_kwargs):
        seen.append(page_number)
        return PageResult(page_number=page_number)

    with patch("app.core.pipeline.process_page_image", fake_process):
        report = _pipeline().process(pdf, page_range=PageRange(5, 8))

    assert seen == [5, 6, 7, 8]
    assert _pages_processed(report) == [5, 6, 7, 8]
    assert report.summary["total_pages"] == 4


def test_without_a_range_the_whole_pdf_is_processed(tmp_path):
    pdf = _pdf(tmp_path / "book.pdf", 6)

    with patch(
        "app.core.pipeline.process_page_image",
        side_effect=lambda _img, number, *a, **k: PageResult(page_number=number),
    ):
        report = _pipeline().process(pdf)

    assert _pages_processed(report) == [1, 2, 3, 4, 5, 6]


def test_an_open_range_runs_to_the_last_page(tmp_path):
    pdf = _pdf(tmp_path / "book.pdf", 6)

    with patch(
        "app.core.pipeline.process_page_image",
        side_effect=lambda _img, number, *a, **k: PageResult(page_number=number),
    ):
        report = _pipeline().process(pdf, page_range=PageRange(4))

    assert _pages_processed(report) == [4, 5, 6]


def test_a_range_past_the_end_of_the_pdf_is_clamped(tmp_path):
    pdf = _pdf(tmp_path / "book.pdf", 3)

    with patch(
        "app.core.pipeline.process_page_image",
        side_effect=lambda _img, number, *a, **k: PageResult(page_number=number),
    ):
        report = _pipeline().process(pdf, page_range=PageRange(2, 99))

    assert _pages_processed(report) == [2, 3]


def test_the_alignment_reference_comes_from_inside_the_range(tmp_path):
    """Alinear un tramo contra una página que no se procesa no tiene sentido."""
    pdf = _pdf(tmp_path / "book.pdf", 10)
    rendered: list[int] = []
    original = Pipeline._process_sequential

    def spy(self, path, first, total, reference, *args, **kwargs):
        rendered.append(first)
        return original(self, path, first, total, reference, *args, **kwargs)

    with patch(
        "app.core.pipeline.process_page_image",
        side_effect=lambda _img, number, *a, **k: PageResult(page_number=number),
    ), patch.object(Pipeline, "_process_sequential", spy), patch.object(
        Pipeline, "_calibrate", return_value=(None, None)
    ):
        pipeline = _pipeline()
        pipeline.reference_page = 1
        pipeline.process(pdf, page_range=PageRange(6, 9))

    assert rendered == [6]


def test_the_parallel_worker_indexes_the_anchors_from_the_first_page(tmp_path):
    """Las anclas cubren el tramo: la página 6 usa la primera del tramo."""
    from app.core import pipeline as pipeline_module
    from app.vision.alignment import TransformResult

    anchors = [TransformResult(reliable=True, tx=float(i)) for i in range(4)]
    pipeline_module._WORKER_STATE.clear()
    pipeline_module._WORKER_STATE.update({
        "pdf_path": tmp_path / "book.pdf",
        "config": _config(),
        "engine": _FakeEngine(),
        "template": Template(name="empty"),
        "reference": None,
        "transforms": anchors,
        "own_reliability": [True] * 4,
        "printed_mask": None,
        "slot_map": {},
        "first_page": 6,
    })
    used = {}

    def fake_process_page(_path, page_number, *_args, **kwargs):
        used["transform"] = kwargs["transform"]
        return PageResult(page_number=page_number)

    with patch.object(pipeline_module, "process_page", fake_process_page):
        pipeline_module._process_page_worker(8)

    assert used["transform"] is anchors[2]

    pipeline_module._WORKER_STATE.clear()


def test_the_batch_range_spans_files_and_skips_the_ones_outside(tmp_path):
    """8-22 de un lote 10+20+5: cola del primero, cabeza del segundo."""
    paths = [
        _pdf(tmp_path / "a.pdf", 10),
        _pdf(tmp_path / "b.pdf", 20),
        _pdf(tmp_path / "c.pdf", 5),
    ]
    calls: list[tuple[str, int, int | None]] = []

    class _FakePipeline:
        vlm_stats = {"enabled": False}

        def __init__(self, *_args, **_kwargs):
            self.on_progress = None

        def process(self, path, page_range=None, should_cancel=None):
            selection = page_range or PageRange()
            calls.append((Path(path).name, selection.first, selection.last))
            from app.models.schemas import ValidationReport

            return ValidationReport(
                pdf_path=str(path), template_name="empty", pages=[]
            )

    class _Pool:
        max_workers = 4

        def temporary_path(self, name):
            return tmp_path / name

    with patch("app.core.pipeline.Pipeline", _FakePipeline):
        reports, stats = process_pdf_batch(
            paths, _config(), Template(name="empty"), _Pool(), _FakeEngine(),
            page_range=PageRange(8, 22),
        )

    assert calls == [("a.pdf", 8, 10), ("b.pdf", 1, 12)]
    assert [Path(r.pdf_path).name for r in reports] == ["a.pdf", "b.pdf"]
    assert len(stats) == 2


def test_a_batch_range_outside_the_input_returns_nothing(tmp_path):
    paths = [_pdf(tmp_path / "a.pdf", 4)]

    class _Pool:
        max_workers = 1

        def temporary_path(self, name):
            return tmp_path / name

    reports, stats = process_pdf_batch(
        paths, _config(), Template(name="empty"), _Pool(), _FakeEngine(),
        page_range=PageRange(50, 60),
    )

    assert reports == [] and stats == []


def test_the_batch_progress_totals_only_the_selected_pages(tmp_path):
    """La barra global cuenta las páginas del rango, no las del lote."""
    paths = [
        _pdf(tmp_path / "a.pdf", 10),
        _pdf(tmp_path / "b.pdf", 20),
    ]
    totals: list[int] = []
    per_file: list[tuple[int, int, int]] = []

    class _ProgressPipeline:
        vlm_stats = {"enabled": False}

        def __init__(self, *_args, **_kwargs):
            self.on_progress = None

        def process(self, path, page_range=None, should_cancel=None):
            from app.models.schemas import ValidationReport

            selection = page_range or PageRange()
            numbers = list(range(selection.first, (selection.last or 0) + 1))
            for done, _number in enumerate(numbers, start=1):
                self.on_progress(done, len(numbers), "…")
            return ValidationReport(
                pdf_path=str(path), template_name="empty",
                pages=[PageResult(page_number=n) for n in numbers],
            )

    class _Pool:
        # Más workers que archivos: el planificador reparte páginas y el
        # lote es quien lleva el contador global.
        max_workers = 4

        def temporary_path(self, name):
            return tmp_path / name

    with patch("app.core.pipeline.Pipeline", _ProgressPipeline):
        process_pdf_batch(
            paths, _config(), Template(name="empty"), _Pool(), _FakeEngine(),
            page_range=PageRange(6, 17),
            on_progress=lambda _done, total, _msg: totals.append(total),
            on_file_progress=lambda *args: per_file.append(args),
        )

    # 5 páginas del primero (6-10) + 7 del segundo (1-7) = 12.
    assert set(totals) == {12}
    assert (1, 5, 5) in per_file and (2, 7, 7) in per_file

"""Escalabilidad y fallback del perfil de rendimiento C."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import numpy as np
import cv2
import pymupdf as fitz

from app.core.config import AppConfig
from app.core.pipeline import OcrProcessPool, Pipeline, process_pdf_batch
from app.models.schemas import PageResult, ValidationReport
from app.ocr.engine import TesseractOcrEngine
from app.templates.schema import Template
from app.vision.alignment import TransformResult, warp_with_transform


class _TrackingExecutor:
    def __init__(self, workers: int):
        self._executor = ThreadPoolExecutor(max_workers=workers)
        self._lock = threading.Lock()
        self.outstanding = 0
        self.max_outstanding = 0

    def submit(self, function, *args, **kwargs):
        with self._lock:
            self.outstanding += 1
            self.max_outstanding = max(self.max_outstanding, self.outstanding)
        future = self._executor.submit(function, *args, **kwargs)

        def completed(_future):
            with self._lock:
                self.outstanding -= 1

        future.add_done_callback(completed)
        return future

    def shutdown(self):
        self._executor.shutdown(wait=True)


class _FakePool:
    def __init__(self, root: Path, workers: int):
        self.max_workers = workers
        self.executor = _TrackingExecutor(workers)
        self.root = root

    def temporary_path(self, name: str) -> Path:
        return self.root / name

    def prepare(self, _state: dict) -> Path:
        path = self.root / "state.pickle"
        path.touch()
        return path

    @staticmethod
    def release(path: Path) -> None:
        path.unlink(missing_ok=True)


class _FakeRenderer:
    def __init__(self, _path):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    @staticmethod
    def page_count() -> int:
        return 50


class _FakeEngine:
    name = "fake"


def _report(path: Path, cancelled: bool = False) -> ValidationReport:
    return ValidationReport(
        pdf_path=str(path),
        template_name="empty",
        pages=[PageResult(page_number=1)],
        cancelled=cancelled,
        summary={"total_pages": 1},
    )


def _fixture_pdf(path: Path) -> None:
    document = fitz.open()
    page = document.new_page(width=220, height=300)
    page.insert_text((50, 80), "12 JUL 26", fontsize=18)
    document.save(path)
    document.close()


def test_mass_scheduler_keeps_only_one_pdf_per_worker_in_flight(tmp_path):
    paths = [tmp_path / f"book_{index:04d}.pdf" for index in range(1000)]
    pool = _FakePool(tmp_path, workers=4)
    starts = []

    def fake_worker(path, *_args):
        time.sleep(0.0005)
        return _report(path), {"enabled": False}

    with patch("app.core.pipeline.PdfPageRenderer", _FakeRenderer), patch(
        "app.core.pipeline._process_pdf_worker", side_effect=fake_worker
    ):
        reports, stats = process_pdf_batch(
            paths,
            AppConfig(),
            Template(name="empty"),
            pool,
            _FakeEngine(),
            on_file_started=lambda index, total, name: starts.append(
                (index, total, name)
            ),
        )
    pool.executor.shutdown()

    assert len(reports) == len(stats) == len(paths) == 1000
    assert [Path(report.pdf_path).name for report in reports] == [
        path.name for path in paths
    ]
    assert pool.executor.max_outstanding <= pool.max_workers
    assert starts[0][:2] == (1, 1000)
    assert starts[-1][:2] == (1000, 1000)


def test_file_parallelism_runs_in_the_real_persistent_process_pool(tmp_path):
    paths = [tmp_path / "first.pdf", tmp_path / "second.pdf"]
    for path in paths:
        _fixture_pdf(path)
    config = AppConfig(
        dpi=100,
        date_dpi=100,
        blank_threshold=0,
        deskew=False,
        align=False,
        remove_printed=False,
        date_ocr_fallback=False,
        date_slot_ocr=False,
        ocr_engine="tesseract",
        ocr_lang="eng",
    )
    engine = TesseractOcrEngine(lang="eng", tesseract_cmd="tesseract.exe")

    with OcrProcessPool(2, config, "tesseract", "eng", 1) as pool:
        reports, stats = process_pdf_batch(
            paths, config, Template(name="empty"), pool, engine
        )

    assert [Path(report.pdf_path) for report in reports] == paths
    assert [len(report.pages) for report in reports] == [1, 1]
    assert len(stats) == 2


def test_mass_scheduler_retries_a_failed_file_with_profile_b(tmp_path):
    paths = [tmp_path / f"book_{index}.pdf" for index in range(12)]
    failed = paths[5]
    pool = _FakePool(tmp_path, workers=3)

    def fake_worker(path, *_args):
        if path == failed:
            raise RuntimeError("fallo C simulado")
        return _report(path), {"enabled": False}

    class FakePipeline:
        def __init__(self, *_args, **_kwargs):
            self.vlm_stats = {"enabled": False, "fallback": True}

        def process(self, path, **_kwargs):
            return _report(path)

    with patch("app.core.pipeline.PdfPageRenderer", _FakeRenderer), patch(
        "app.core.pipeline._process_pdf_worker", side_effect=fake_worker
    ), patch("app.core.pipeline.config_for_pdf", side_effect=lambda config, _p: config), patch(
        "app.core.pipeline.Pipeline", FakePipeline
    ):
        reports, stats = process_pdf_batch(
            paths, AppConfig(), Template(name="empty"), pool, _FakeEngine()
        )
    pool.executor.shutdown()

    assert len(reports) == 12
    assert reports[5].pdf_path == str(failed)
    assert stats[5]["fallback"] is True


def test_profile_c_uses_page_parallelism_for_a_small_batch(tmp_path):
    paths = [tmp_path / "book_1.pdf", tmp_path / "book_2.pdf"]
    pool = _FakePool(tmp_path, workers=4)
    created = []

    class FakePipeline:
        def __init__(self, *_args, workers, **_kwargs):
            self.workers = workers
            self.vlm_stats = {"enabled": False}
            self.on_progress = None
            created.append(self)

        def process(self, path, **_kwargs):
            if self.on_progress is not None:
                self.on_progress(1, 1, f"Procesado {Path(path).name}")
            return _report(path)

    with patch("app.core.pipeline.PdfPageRenderer", _FakeRenderer), patch(
        "app.core.pipeline._process_pdf_worker"
    ) as file_worker, patch(
        "app.core.pipeline.config_for_pdf", side_effect=lambda config, _p: config
    ), patch("app.core.pipeline.Pipeline", FakePipeline):
        reports, _stats = process_pdf_batch(
            paths, AppConfig(), Template(name="empty"), pool, _FakeEngine()
        )
    pool.executor.shutdown()

    assert [report.pdf_path for report in reports] == [str(path) for path in paths]
    assert [pipeline.workers for pipeline in created] == [4, 4]
    file_worker.assert_not_called()


def test_mass_scheduler_cancellation_does_not_fill_the_queue(tmp_path):
    paths = [tmp_path / f"book_{index}.pdf" for index in range(100)]
    pool = _FakePool(tmp_path, workers=2)
    checks = 0

    def should_cancel():
        nonlocal checks
        checks += 1
        return checks >= 2

    def fake_worker(path, *_args):
        time.sleep(0.3)
        return _report(path, cancelled=True), {"enabled": False}

    with patch("app.core.pipeline.PdfPageRenderer", _FakeRenderer), patch(
        "app.core.pipeline._process_pdf_worker", side_effect=fake_worker
    ):
        reports, _stats = process_pdf_batch(
            paths,
            AppConfig(),
            Template(name="empty"),
            pool,
            _FakeEngine(),
            should_cancel=should_cancel,
        )
    pool.executor.shutdown()

    assert len(reports) <= pool.max_workers
    assert pool.executor.max_outstanding <= pool.max_workers
    assert not pool.temporary_path("cancel_mass_batch.flag").exists()


def test_page_scheduler_bounds_futures_for_a_large_pdf(tmp_path):
    pool = _FakePool(tmp_path, workers=4)
    pipeline = Pipeline(
        AppConfig(align=False),
        _FakeEngine(),
        Template(name="empty"),
        workers=4,
        process_pool=pool,
    )

    with patch(
        "app.core.pipeline._process_page_worker",
        side_effect=lambda page, _state: PageResult(page_number=page),
    ):
        pages = pipeline._process_parallel(
            Path("large.pdf"),
            1000,
            np.zeros((2, 2, 3), np.uint8),
        )
    pool.executor.shutdown()

    assert len(pages) == 1000
    assert [page.page_number for page in pages] == list(range(1, 1001))
    assert pool.executor.max_outstanding <= 12


def test_grayscale_calibration_buffer_preserves_scanned_ink_mask():
    gray = np.tile(np.arange(0, 255, dtype=np.uint8), (96, 1))
    scanned_bgr = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    transform = TransformResult(
        tx=1.7, ty=-2.3, rot=0.35, scale=1.001, reliable=True
    )
    size = (scanned_bgr.shape[1], scanned_bgr.shape[0])

    old_mask = cv2.cvtColor(
        warp_with_transform(scanned_bgr, transform, size),
        cv2.COLOR_BGR2GRAY,
    ) < 185
    new_mask = warp_with_transform(gray, transform, size) < 185

    assert np.array_equal(new_mask, old_mask)
    assert gray.nbytes * 3 == scanned_bgr.nbytes

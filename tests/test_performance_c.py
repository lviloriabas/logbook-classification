"""Escalabilidad y fallback del perfil de rendimiento C."""

from __future__ import annotations

import csv
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import numpy as np
import cv2
import pymupdf as fitz
import pytest

from app.core.config import AppConfig
from app.core.pipeline import OcrProcessPool, Pipeline, process_pdf_batch
from app.models.schemas import PageResult, ValidationReport
from app.ocr.engine import TesseractOcrEngine
from app.reports.csv_reporter import CsvReporter
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


def test_the_csv_time_column_adds_up_to_the_clock_of_a_real_run(tmp_path):
    """La suma de ``time_ms`` es lo que tardó la corrida, no varias veces eso.

    Con un proceso por archivo cada bitácora mide su propio reloj mientras
    comparte la CPU con las demás: sumarlos contaba el mismo minuto una vez
    por archivo. Esta prueba usa el pool real porque el fallo solo aparece
    cuando los archivos se solapan de verdad en procesos distintos.
    """
    paths = [tmp_path / f"book_{index}.pdf" for index in range(4)]
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

    started = time.perf_counter()
    with OcrProcessPool(2, config, "tesseract", "eng", 1) as pool:
        reports, _stats = process_pdf_batch(
            paths, config, Template(name="empty"), pool, engine
        )
    elapsed_ms = (time.perf_counter() - started) * 1000

    assert all(report.started_at > 0 for report in reports)
    csv_path = tmp_path / "out.csv"
    CsvReporter().write(reports, csv_path)
    with open(csv_path, encoding="utf-8-sig", newline="") as fh:
        times = [float(row["time_ms"]) for row in csv.DictReader(fh)]

    assert len(times) == len(reports)
    # Nunca puede sumar más que el reloj de la corrida (el error original:
    # cuatro archivos en dos procesos sumaban del orden del doble).
    assert sum(times) <= elapsed_ms
    # Y cubre la corrida salvo el arranque y el cierre del pool, que no son
    # tiempo imputable a ninguna página.
    assert sum(times) >= elapsed_ms * 0.5
    assert sum(times) == pytest.approx(CsvReporter.run_wall_ms(reports), rel=0.01)


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


def test_file_parallelism_reports_progress_of_every_file_in_flight(tmp_path):
    """Con un PDF por proceso el avance sigue siendo página a página.

    Antes la única señal era el fin de cada archivo: las barras se quedaban
    quietas y luego saltaban al 100 %, y la barra global se repartía en orden
    de archivo aunque avanzaran otros.
    """
    paths = [tmp_path / f"book_{index}.pdf" for index in range(4)]
    pool = _FakePool(tmp_path, workers=4)
    updates: list[tuple[int, int, int]] = []

    def fake_worker(path, *args):
        # El worker real escribe su contador en la ruta que recibe del padre.
        counter = Path(args[-1])
        for page in range(1, 51):
            counter.write_text(f"{page}/50", encoding="ascii")
            time.sleep(0.005)
        return _report(path), {"enabled": False}

    with patch("app.core.pipeline.PdfPageRenderer", _FakeRenderer), patch(
        "app.core.pipeline._process_pdf_worker", side_effect=fake_worker
    ):
        process_pdf_batch(
            paths,
            AppConfig(),
            Template(name="empty"),
            pool,
            _FakeEngine(),
            on_file_progress=lambda index, done, total: updates.append(
                (index, done, total)
            ),
        )
    pool.executor.shutdown()

    assert {index for index, _done, _total in updates} == {1, 2, 3, 4}
    # Cada archivo pasó por avances intermedios, no solo 0 % y final.
    for file_index in (1, 2, 3, 4):
        done = [d for index, d, _t in updates if index == file_index]
        assert done[0] == 0
        assert any(0 < value < 50 for value in done)
    assert all(total == 50 for _index, _done, total in updates)
    assert not list(tmp_path.glob("pages_*.count"))


def test_page_parallelism_reports_the_same_per_file_progress(tmp_path):
    paths = [tmp_path / "book_1.pdf", tmp_path / "book_2.pdf"]
    pool = _FakePool(tmp_path, workers=4)
    updates: list[tuple[int, int, int]] = []

    class FakePipeline:
        def __init__(self, *_args, **_kwargs):
            self.vlm_stats = {"enabled": False}
            self.on_progress = None

        def process(self, path, **_kwargs):
            for page in range(1, 51):
                self.on_progress(page, 50, f"Procesando página {page}/50")
            return _report(path)

    with patch("app.core.pipeline.PdfPageRenderer", _FakeRenderer), patch(
        "app.core.pipeline.config_for_pdf", side_effect=lambda config, _p: config
    ), patch("app.core.pipeline.Pipeline", FakePipeline):
        process_pdf_batch(
            paths,
            AppConfig(),
            Template(name="empty"),
            pool,
            _FakeEngine(),
            on_file_progress=lambda index, done, total: updates.append(
                (index, done, total)
            ),
        )
    pool.executor.shutdown()

    assert (1, 0, 50) in updates and (2, 0, 50) in updates
    assert (1, 25, 50) in updates and (2, 25, 50) in updates
    # La forma del avance es idéntica a la de la estrategia por archivos.
    assert all(total == 50 for _index, _done, total in updates)


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
            1,
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

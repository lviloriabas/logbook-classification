"""Worker en QThread para ejecutar el pipeline sin congelar la GUI."""

from __future__ import annotations

import traceback
from pathlib import Path
from typing import List, Union

from loguru import logger
from PySide6.QtCore import QThread, Signal

from app.core.config import AppConfig
from app.models.schemas import ValidationReport
from app.templates.manager import TemplateManager
from app.validation.book_corrector import correct_matricula_by_book
from app.validation.date_corrector import correct_dates_by_book


class PipelineWorker(QThread):
    """Ejecuta el pipeline de validación en un hilo separado.

    Puede procesar una sola bitácora o un lote. Con un lote, los
    correctores por libro (matrícula/fecha) se aplican sobre todos los
    reportes antes de emitir ``succeeded``. Al cancelar (requestInterruption)
    se emite ``succeeded`` con los reportes ya procesados (parciales), para
    que la GUI guarde el trabajo en curso.
    """

    progress = Signal(int, int, str)
    file_started = Signal(int, int, str)
    file_finished = Signal(int, object)
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        pdf_paths: Union[Path, List[Path]],
        template_path: Path,
        config: AppConfig,
        max_pages: int | None = None,
        reference_page: int = 1,
        workers: int = 1,
        cpu_threads: int | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        if isinstance(pdf_paths, Path):
            pdf_paths = [pdf_paths]
        self.pdf_paths = [Path(p) for p in pdf_paths]
        self.template_path = Path(template_path)
        self.config = config
        self.max_pages = max_pages
        self.reference_page = max(1, reference_page)
        self.workers = max(1, workers)
        self.cpu_threads = cpu_threads
        self._progress_offset = 0
        self._prev_total = 0
        self._progress_file = 0
        self._current_file_index = 0
        self.reports: List[ValidationReport] = []
        self.vlm_stats: List[dict] = []

    def run(self) -> None:
        """Punto de entrada del hilo."""
        try:
            from contextlib import nullcontext

            from app.core.pipeline import (
                OcrProcessPool,
                Pipeline,
                process_pdf_batch,
            )
            from app.core.config import config_for_pdf
            from app.ocr.engine import create_engine

            template = TemplateManager().load(self.template_path)
            engine_kwargs = {}
            if self.cpu_threads is not None:
                engine_kwargs["cpu_threads"] = self.cpu_threads
            if self.config.ocr_rec_model:
                engine_kwargs["rec_model"] = self.config.ocr_rec_model
            if self.config.ocr_det_model:
                engine_kwargs["det_model"] = self.config.ocr_det_model
            engine = create_engine(
                self.config.ocr_engine,
                lang=self.config.ocr_lang,
                **engine_kwargs,
            )
            date_engine = None
            if self.config.date_engine_name:
                date_engine = create_engine(
                    self.config.date_engine_name,
                    lang=self.config.ocr_lang,
                    **engine_kwargs,
                )
            reports: List[ValidationReport] = []
            pool_context = (
                OcrProcessPool(
                    self.workers,
                    self.config,
                    self.config.ocr_engine,
                    self.config.ocr_lang,
                    self.cpu_threads,
                    self.config.date_engine_name or None,
                )
                if self.workers > 1
                else nullcontext(None)
            )
            with pool_context as process_pool:
                if process_pool is not None:
                    reports, self.vlm_stats = process_pdf_batch(
                        self.pdf_paths,
                        self.config,
                        template,
                        process_pool,
                        engine,
                        date_engine=date_engine,
                        max_pages=self.max_pages,
                        reference_page=self.reference_page,
                        should_cancel=self.isInterruptionRequested,
                        on_file_started=lambda index, total, name: (
                            self.file_started.emit(index, total, name)
                        ),
                        on_file_finished=lambda index, report: (
                            self.file_finished.emit(index, report)
                        ),
                        on_progress=lambda done, total, message: (
                            self.progress.emit(done, total, message)
                        ),
                    )
                    self.reports = list(reports)
                else:
                    logger.info(
                        "[Perfil C] activo | estrategia=secuencial | workers=1"
                    )
                    for index, pdf_path in enumerate(self.pdf_paths):
                        if self.isInterruptionRequested():
                            break
                        self._current_file_index = index + 1
                        self.file_started.emit(
                            index + 1, len(self.pdf_paths), pdf_path.name
                        )
                        try:
                            file_config = config_for_pdf(self.config, pdf_path)
                        except (FileNotFoundError, ValueError):
                            # El Pipeline conserva el error autoritativo. Este
                            # fallback permite inyectar pipelines de prueba.
                            file_config = self.config
                        pipeline = Pipeline(
                            file_config,
                            engine,
                            template,
                            on_progress=self._on_progress,
                            workers=self.workers,
                            cpu_threads=self.cpu_threads,
                            date_engine=date_engine,
                            process_pool=process_pool,
                            reference_page=self.reference_page,
                        )
                        report: ValidationReport = pipeline.process(
                            pdf_path,
                            max_pages=self.max_pages,
                            should_cancel=self.isInterruptionRequested,
                        )
                        reports.append(report)
                        self.vlm_stats.append(pipeline.vlm_stats)
                        self.reports = list(reports)
                        self.file_finished.emit(index + 1, report)
                        if report.cancelled:
                            break
            correct_matricula_by_book(reports)
            correct_dates_by_book(reports)
            if self.config.verify_fleet:
                from app.validation.fleet import verify_reports_against_fleet

                verify_reports_against_fleet(reports, self.config.fleet_file)
            self.reports = reports
            self.succeeded.emit(reports)
        except Exception as exc:  # noqa: BLE001 - la GUI muestra el error
            self.failed.emit(f"{exc}\n{traceback.format_exc()}")

    def _on_progress(self, done: int, total: int, message: str) -> None:
        """Acumula el progreso entre bitácoras para un conteo global.

        Cada Pipeline emite ``(done, total)`` relativo a su propio PDF; al
        cambiar de archivo el contador vuelve a empezar, así que se acumula
        un desplazamiento con el total de la bitácora anterior. El mensaje se
        antepone con el archivo actual para mostrar el avance por bitácora.
        """
        if self._current_file_index != self._progress_file:
            self._progress_offset += self._prev_total
            self._progress_file = self._current_file_index
            self._prev_total = total
        if total < self._prev_total:
            self._progress_offset += self._prev_total
        self._prev_total = total
        prefix = ""
        if self._current_file_index and self.pdf_paths:
            name = self.pdf_paths[self._current_file_index - 1].name
            prefix = (f"Archivo {self._current_file_index}/"
                      f"{len(self.pdf_paths)}: {name} — ")
        self.progress.emit(self._progress_offset + done,
                           self._progress_offset + total, prefix + message)


class PreprocessWorker(QThread):
    """Aplica el preprocesamiento de página sin ejecutar OCR."""

    progress = Signal(int, int, str)
    page_ready = Signal(str, int, object)
    succeeded = Signal(bool)
    failed = Signal(str)

    def __init__(
        self,
        pdf_paths: List[Path],
        config: AppConfig,
        max_pages: int | None = None,
        reference_page: int = 1,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.pdf_paths = [Path(path) for path in pdf_paths]
        self.config = config
        self.max_pages = max_pages
        self.reference_page = max(1, reference_page)

    def run(self) -> None:
        """Renderiza, endereza y alinea las páginas para su revisión visual."""
        try:
            from app.vision.alignment import apply_transform, compute_similarity_transform
            from app.core.config import config_for_pdf
            from app.vision.pdf_loader import PdfPageRenderer, page_count
            from app.vision.preprocessing import deskew

            total = 0
            counts: list[int] = []
            for pdf_path in self.pdf_paths:
                count = page_count(pdf_path)
                if self.max_pages is not None:
                    count = min(count, self.max_pages)
                counts.append(count)
                total += count

            done = 0
            for pdf_path, count in zip(self.pdf_paths, counts):
                if self.isInterruptionRequested():
                    break

                file_config = config_for_pdf(self.config, pdf_path)
                with PdfPageRenderer(pdf_path) as renderer:
                    reference = None
                    if file_config.align and count:
                        reference = renderer.render_page(
                            min(self.reference_page, count), file_config.dpi
                        )

                    for page_number in range(1, count + 1):
                        if self.isInterruptionRequested():
                            break
                        self.progress.emit(
                            done,
                            total,
                            f"Preprocesando {pdf_path.name}: "
                            f"página {page_number}/{count}",
                        )
                        image = renderer.render_page(
                            page_number, file_config.dpi
                        )
                        if file_config.deskew:
                            image, _angle = deskew(image)
                        if file_config.align and reference is not None:
                            transform = compute_similarity_transform(
                                image, reference, file_config
                            )
                            if transform.reliable:
                                image = apply_transform(image, transform)
                        done += 1
                        self.page_ready.emit(str(pdf_path), page_number, image)
                        self.progress.emit(
                            done,
                            total,
                            f"Preprocesando {pdf_path.name}: "
                            f"página {page_number}/{count}",
                        )
            self.succeeded.emit(self.isInterruptionRequested())
        except Exception as exc:  # noqa: BLE001 - la GUI muestra el error
            self.failed.emit(f"{exc}\n{traceback.format_exc()}")


class OutputsWorker(QThread):
    """Genera las salidas de una corrida sin bloquear la interfaz."""

    succeeded = Signal(object)
    failed = Signal(str)
    progress = Signal(str, int)

    def __init__(
        self,
        reports: List[ValidationReport],
        options: OutputOptions,
        vlm_stats: List[dict] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.reports = reports
        self.options = options
        self.vlm_stats = vlm_stats or []

    def run(self) -> None:
        """Punto de entrada del hilo de generación de salidas."""
        try:
            from app.reports.outputs import write_outputs

            output_dir = write_outputs(
                self.reports,
                self.options,
                vlm_stats=self.vlm_stats,
                on_stage=self._on_stage,
            )
        except Exception as exc:  # noqa: BLE001 - la GUI muestra el error
            self.failed.emit(f"{exc}\n{traceback.format_exc()}")
        else:
            self.succeeded.emit(output_dir)

    def _on_stage(self, message: str, percent: int) -> None:
        self.progress.emit(message, percent)

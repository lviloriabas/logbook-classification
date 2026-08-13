"""Worker en QThread para ejecutar el pipeline sin congelar la GUI."""

from __future__ import annotations

import traceback
from pathlib import Path
from typing import List, Union

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
            from app.core.pipeline import Pipeline
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
            for index, pdf_path in enumerate(self.pdf_paths):
                if self.isInterruptionRequested():
                    break
                self._current_file_index = index + 1
                self.file_started.emit(index + 1, len(self.pdf_paths),
                                       pdf_path.name)
                pipeline = Pipeline(
                    self.config,
                    engine,
                    template,
                    on_progress=self._on_progress,
                    workers=self.workers,
                    cpu_threads=self.cpu_threads,
                    date_engine=date_engine,
                )
                if self.reference_page and self.config.align:
                    from app.vision.pdf_loader import render_page

                    try:
                        pipeline.reference_image = render_page(
                            pdf_path, self.reference_page, self.config.dpi
                        )
                    except Exception:  # noqa: BLE001 - página inválida
                        pipeline.reference_image = None
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
            from app.vision.pdf_loader import page_count, render_page
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

                reference = None
                if self.config.align and count:
                    reference = render_page(
                        pdf_path,
                        min(self.reference_page, count),
                        self.config.dpi,
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
                    image = render_page(pdf_path, page_number, self.config.dpi)
                    if self.config.deskew:
                        image, _angle = deskew(image)
                    if self.config.align and reference is not None:
                        transform = compute_similarity_transform(
                            image, reference, self.config
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

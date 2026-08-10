"""Orquestador del pipeline de validación de bitácoras.

Flujo por página:
    render → blank → deskew → alineación → recorte por región
    → OCR/firma/checkbox (OCR en lote por página) → postproceso
    → reglas de validación.

El pipeline depende de interfaces (OcrEngine, Template), por lo que se
pueden añadir motores VL (Qwen), nuevos tipos de campo o paralelismo
sin modificar esta clase.

Paralelismo: con ``workers > 1`` las páginas se reparten entre procesos
worker (cada uno con su propio motor OCR), porque PaddleOCR no suelta el
GIL y los hilos no escalan. ``cpu_threads`` controla los hilos internos
de cada motor (ajustar según la máquina: núcleos / workers).
"""

from __future__ import annotations

import re
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import cv2
import numpy as np
from loguru import logger

from app.core.config import AppConfig
from app.models.schemas import (
    FieldResult,
    PageResult,
    Status,
    ValidationReport,
)
from app.ocr.engine import OcrEngine
from app.ocr.date_ocr import ocr_fallback, read_date_slots
from app.ocr.regional import ocr_regions
from app.templates.schema import FieldType, Template
from app.utils.postprocess import (
    WEAK_MATRICULA_NOTE,
    apply_postprocess,
    combine_date,
)
from app.validation.validator import validate_page
from app.vision.alignment import (
    TransformResult,
    apply_transform,
    compute_similarity_transform,
    warp_with_transform,
)
from app.vision.blank_detection import is_blank
from app.vision.checkbox import detect_checkbox
from app.vision.ink_extent import crop_to_ink, strip_date_label
from app.vision.pdf_loader import page_count, render_page
from app.vision.preprocessing import crop_region, deskew, upscale_for_ocr
from app.vision.signature import UNCLEAR, detect_signature
from app.vision.date_slots import build_slot_maps, crop_slots, localize_slot

ProgressCallback = Callable[[int, int, str], None]

_ORDER = {Status.OK: 0, Status.WARNING: 1, Status.ERROR: 2}

# Notas de postproceso "blandas": no son fallos del campo, solo avisos.
_SOFT_NOTES = (
    WEAK_MATRICULA_NOTE,
    "month fuzzy",
)

_WORKER_STATE: dict = {}


def _init_worker(config: AppConfig, template: Template, engine_name: str,
                 lang: str, cpu_threads: Optional[int],
                 reference: Optional[np.ndarray], pdf_path: Path,
                 transforms: Optional[List[TransformResult]] = None,
                 own_reliability: Optional[List[bool]] = None,
                 printed_mask: Optional[np.ndarray] = None,
                 slot_map: Optional[dict] = None) -> None:
    """Inicializa un proceso worker: crea su propio motor OCR y guarda el
    estado compartido (config, plantilla, referencia, anclas, PDF)."""
    from app.ocr.engine import create_engine

    _WORKER_STATE["config"] = config
    _WORKER_STATE["template"] = template
    _WORKER_STATE["reference"] = reference
    _WORKER_STATE["pdf_path"] = Path(pdf_path)
    _WORKER_STATE["transforms"] = list(transforms or [])
    _WORKER_STATE["own_reliability"] = list(own_reliability or [])
    _WORKER_STATE["slot_map"] = dict(slot_map or {})
    if printed_mask is not None:
        _WORKER_STATE["printed_mask"] = np.ascontiguousarray(printed_mask)
    kwargs = {}
    if cpu_threads is not None:
        kwargs["cpu_threads"] = cpu_threads
    rec_model = getattr(config, "ocr_rec_model", None)
    if rec_model:
        kwargs["rec_model"] = rec_model
    det_model = getattr(config, "ocr_det_model", None)
    if det_model:
        kwargs["det_model"] = det_model
    _WORKER_STATE["engine"] = create_engine(engine_name, lang=lang, **kwargs)


def _process_page_worker(page_number: int) -> PageResult:
    """Procesa una página dentro de un proceso worker."""
    transforms = _WORKER_STATE.get("transforms") or []
    reliability = _WORKER_STATE.get("own_reliability") or []
    return process_page(
        _WORKER_STATE["pdf_path"],
        page_number,
        _WORKER_STATE["config"],
        _WORKER_STATE["engine"],
        _WORKER_STATE["template"],
        _WORKER_STATE["reference"],
        transform=transforms[page_number - 1]
        if len(transforms) >= page_number else None,
        transform_reliable=reliability[page_number - 1]
        if len(reliability) >= page_number else None,
        printed_mask=_WORKER_STATE.get("printed_mask"),
        slot_map=_WORKER_STATE.get("slot_map"),
    )


def process_page(
    pdf_path: Path,
    page_number: int,
    config: AppConfig,
    engine: OcrEngine,
    template: Template,
    reference: Optional[np.ndarray],
    transform: Optional[TransformResult] = None,
    transform_reliable: Optional[bool] = None,
    printed_mask: Optional[np.ndarray] = None,
    slot_map: Optional[dict] = None,
) -> PageResult:
    """Renderiza y procesa una página del PDF (función de proceso worker)."""
    image = render_page(pdf_path, page_number, config.dpi)
    return process_page_image(image, page_number, config, engine, template,
                              reference, transform, transform_reliable,
                              printed_mask, slot_map)


def process_page_image(
    image: np.ndarray,
    page_number: int,
    config: AppConfig,
    engine: OcrEngine,
    template: Template,
    reference: Optional[np.ndarray],
    transform: Optional[TransformResult] = None,
    transform_reliable: Optional[bool] = None,
    printed_mask: Optional[np.ndarray] = None,
    slot_map: Optional[dict] = None,
) -> PageResult:
    """Procesa una imagen de página completa contra la plantilla."""
    t_start = time.perf_counter()
    page = PageResult(page_number=page_number)
    logger.info(f"[Página {page_number}] Inicio de procesamiento")

    # 1) Página en blanco
    if is_blank(image, config.blank_threshold):
        page.blank = True
        page.status = Status.WARNING
        page.comment = "Blank page detected"
        logger.warning(f"[Página {page_number}] En blanco")
        page.processing_ms = round((time.perf_counter() - t_start) * 1000, 1)
        return page

    # 2) Corrección de inclinación
    if config.deskew:
        image, angle = deskew(image)
        page.skew_angle = round(angle, 3)
        if abs(angle) > 0.05:
            logger.info(f"[Página {page_number}] Deskew: {angle:.2f}°")

    # 3) Alineación con la plantilla (ancla estabilizada por lote si existe)
    if config.align and reference is not None:
        if transform is not None:
            image = apply_transform(image, transform)
            quality = "ok" if transform_reliable else "low"
        else:
            own = compute_similarity_transform(image, reference, config)
            image = apply_transform(image, own)
            quality = "ok" if own.reliable else "low"
        page.alignment_quality = quality
        if quality != "ok":
            logger.warning(f"[Página {page_number}] Alineación: {quality}")

    # 3.5) Preparar una copia sin fondo impreso para firmas y casillas.
    # No se usa para OCR: si varias páginas repiten la misma escritura, el
    # consenso del fondo también la clasifica como impresa.
    analysis_image = image
    if printed_mask is not None:
        if printed_mask.shape != image.shape[:2]:
            mask = cv2.resize(
                printed_mask.astype(np.uint8),
                (image.shape[1], image.shape[0]),
                interpolation=cv2.INTER_NEAREST,
            ).astype(bool)
        else:
            mask = printed_mask
        analysis_image = image.copy()
        analysis_image[mask] = 255

    # 4-7) Campos: recorte → OCR (en lote)/firma/checkbox → postproceso
    ocr_fields = [
        field for field in template.fields
        if field.type not in (FieldType.SIGNATURE, FieldType.CHECKBOX)
    ]
    ocr_results = ocr_regions(engine, image, ocr_fields,
                              config.crop_padding,
                              preprocess=config.crop_preprocess,
                              dpi=config.dpi)
    by_id = {field.id: result
             for field, result in zip(ocr_fields, ocr_results)}

    for field in template.fields:
        if field.type in (FieldType.SIGNATURE, FieldType.CHECKBOX):
            region = crop_region(analysis_image, field, config.crop_padding)
            if field.type is FieldType.SIGNATURE:
                result = detect_signature(region, field, page_number)
            else:
                result = detect_checkbox(region, field, page_number)
        else:
            raw_text, confidence = by_id.get(field.id, ("", 0.0))
            text, note = raw_text, ""
            recovered = False
            recovered_via = ""
            if raw_text and field.postprocess:
                text, note = apply_postprocess(
                    field.id, field.postprocess, raw_text
                )
                if note == WEAK_MATRICULA_NOTE:
                    confidence = round(confidence * 0.4, 3)
            # Redundancia OCR: si la lectura principal no produce un valor
            # válido en un campo crítico (fecha, matrícula, log_number), se
            # reintenta con Tesseract restringido (dígitos/letras/matrícula)
            # sobre la tinta localizada.
            if (
                config.date_ocr_fallback
                and field.postprocess in _CRITICAL_POSTPROCESS
                and not text
            ):
                fb = _critical_ocr_fallback(
                    field, image, config.crop_padding,
                    preprocess=config.crop_preprocess,
                    dpi=config.dpi,
                )
                if fb is not None:
                    fb_text, fb_conf = fb
                    text, fb_note = apply_postprocess(
                        field.id, field.postprocess, fb_text
                    )
                    if text:
                        confidence = fb_conf
                        note = fb_note
                        recovered = True
                        recovered_via = "tesseract"
                        logger.info(
                            f"[Página {page_number}] {field.id}: "
                            f"valor recuperado con OCR de respaldo "
                            f"({fb_text!r})"
                        )
            # Redundancia por ranuras: la casilla de fecha está partida por
            # separadores verticales impresos; se lee carácter por carácter
            # con restricciones (dígitos / abreviatura de mes).
            if (
                config.date_slot_ocr
                and field.postprocess in ("day", "month", "year")
                and _needs_slot_recovery(field.id, field.postprocess, text)
                and slot_map is not None
                and field.id in slot_map
            ):
                fb = _slot_ocr_fallback(
                    field, image, config.crop_padding, slot_map[field.id],
                    preprocess=config.crop_preprocess,
                    dpi=config.dpi,
                )
                if fb is not None:
                    fb_text, fb_conf = fb
                    text, fb_note = apply_postprocess(
                        field.id, field.postprocess, fb_text
                    )
                    if text:
                        confidence = fb_conf
                        note = fb_note
                        recovered = True
                        recovered_via = "ranuras"
                        logger.info(
                            f"[Página {page_number}] {field.id}: "
                            f"valor recuperado por ranuras de casilla "
                            f"({fb_text!r})"
                        )
            status = Status.OK
            if note and not any(n in note for n in _SOFT_NOTES):
                status = Status.ERROR
            result = FieldResult(
                page_number=page_number,
                field_id=field.id,
                field_type=field.type.value,
                value=text or None,
                confidence=confidence,
                status=status,
                comment=note,
                raw_value=raw_text or None,
            )
            if recovered and not note:
                result.comment = (
                    f"OCR respaldo ({recovered_via}): {fb_text!r}"
                    if recovered_via else
                    f"OCR respaldo (tesseract): {fb_text!r}"
                )
        page.add_field(result)

    # 7b) Combinar day/month/year en una sola fecha normalizada
    _combine_date_parts(page)

    # 8) Reglas de validación (required/regex/longitud/confianza)
    validate_page(page, template, config)
    logger.info(f"[Página {page_number}] Estado: {page.status.value}")

    page.processing_ms = round((time.perf_counter() - t_start) * 1000, 1)
    return page


_CRITICAL_POSTPROCESS = frozenset(
    {"day", "month", "year", "matricula", "digits"}
)
_TIGHT_CROP_POSTPROCESS = _CRITICAL_POSTPROCESS

# Campos de fecha: se leen sin crop_to_ink (ver _critical_ocr_fallback).
_DATE_FIELDS = frozenset({"day", "month", "year"})


def _needs_slot_recovery(field_id: str, postprocess: str,
                         text: str) -> bool:
    """Si el año salió con un dígito intruso ('216' por el separador de
    casilla o el rabo del 6), el OCR por ranuras puede leer '2'+'6'."""
    if postprocess != "year":
        return False
    return not text or re.fullmatch(r"\d{3}", text or "") is not None


def _critical_ocr_fallback(
    field: "FieldTemplate",
    image: np.ndarray,
    crop_padding: float,
    preprocess: bool = True,
    dpi: Optional[int] = None,
) -> Optional[Tuple[str, float]]:
    """Segunda pasada OCR (Tesseract restringido) para un campo crítico.

    Recorta la región igual que el OCR principal, la localiza por tinta y
    la escala, y delega en ``ocr_fallback``. Devuelve (texto, confianza)
    o None si no se puede leer. ``preprocess=False`` envía el recorte
    crudo (sin crop_to_ink ni upscale). Los campos de fecha (day/month/
    year) se leen sin localizar: se elimina solo la franja superior del
    rótulo impreso.
    """
    try:
        field_padding = (
            0.0 if field.postprocess in _TIGHT_CROP_POSTPROCESS
            else crop_padding
        )
        region = crop_region(image, field, pad=field_padding)
    except ValueError:
        return None
    if preprocess:
        if field.postprocess in _DATE_FIELDS:
            region = strip_date_label(region)
        elif field.localize == "ink":
            localized = crop_to_ink(region, dpi=dpi)
            if localized is not None:
                region = localized
        region = upscale_for_ocr(region, min_side=600)
    return ocr_fallback(field.id, field.postprocess, region)


def _slot_ocr_fallback(
    field: "FieldTemplate",
    image: np.ndarray,
    crop_padding: float,
    slot_spec: dict,
    preprocess: bool = True,
    dpi: Optional[int] = None,
) -> Optional[Tuple[str, float]]:
    """Tercera pasada: OCR por carácter sobre las ranuras de la casilla.

    La casilla lleva separadores verticales impresos entre caracteres;
    con el mapa de ranuras (posiciones fijas derivadas del fondo impreso)
    se recorta cada celda y se lee con Tesseract PSM 10 + whitelist,
    decodificando con restricciones (dígitos o abreviatura de mes).
    ``preprocess=False`` lee las ranuras crudas.
    """
    slots = crop_slots(image, field, 0.0, slot_spec)
    if not slots:
        return None
    if preprocess:
        slots = [localize_slot(slot, dpi=dpi) for slot in slots]
    return read_date_slots(field.id, field.postprocess, slots,
                           preprocess=preprocess)


def _combine_date_parts(page: PageResult) -> None:
    """Combina los campos day/month/year de la página en YYYY/MM/dd.

    La fecha normalizada se guarda en ``page.date``; el campo ``year``
    conserva el año corregido de 2-4 dígitos y day/month sus dígitos.
    Si algún parte falta o es inválida, la nota se deja en el campo
    afectado y la validación de ``required`` lo detectará.
    """
    by_id = {result.field_id: result for result in page.fields}
    day = by_id.get("day")
    month = by_id.get("month")
    year = by_id.get("year")
    if not (day and month and year):
        return
    combined, note = combine_date(day.value, month.value, year.value)
    if note:
        year.comment = note
        page.date = None
        logger.debug(f"[Página {page.page_number}] Fecha: {note}")
    else:
        page.date = combined
        year.comment = ""
        logger.debug(
            f"[Página {page.page_number}] Fecha combinada: {combined}"
        )


class Pipeline:
    """Procesa un PDF completo contra una plantilla."""

    def __init__(
        self,
        config: AppConfig,
        engine: OcrEngine,
        template: Template,
        reference_image: Optional[np.ndarray] = None,
        on_progress: Optional[ProgressCallback] = None,
        workers: int = 1,
        cpu_threads: Optional[int] = None,
    ) -> None:
        self.config = config
        self.engine = engine
        self.template = template
        self.reference_image = reference_image
        self.on_progress = on_progress
        self.workers = max(1, workers)
        self.cpu_threads = cpu_threads
        self._printed_mask: Optional[np.ndarray] = None
        self._should_cancel: Optional[Callable[[], bool]] = None
        self.vlm_stats: dict = {"enabled": False}
        self.calibration_ms: float = 0.0

    def _is_cancelled(self) -> bool:
        return self._should_cancel is not None and self._should_cancel()

    def process(self, pdf_path: Path,
                max_pages: Optional[int] = None,
                should_cancel: Optional[Callable[[], bool]] = None
                ) -> ValidationReport:
        """Procesa el PDF completo y devuelve el reporte de validación.

        Args:
            pdf_path: Ruta del PDF escaneado.
            max_pages: Procesar solo las primeras N páginas (opcional).
            should_cancel: Callable sin argumentos que devuelve True cuando
                se pidió la cancelación; el pipeline verifica entre páginas
                y devuelve un reporte parcial con ``cancelled=True``.

        Returns:
            ValidationReport con el resumen y los resultados por página.
        """
        pdf_path = Path(pdf_path)
        self._should_cancel = should_cancel
        t_start = time.perf_counter()
        logger.info(f"[Pipeline] Inicio: {pdf_path.name} "
                    f"(plantilla {self.template.name}, "
                    f"workers={self.workers})")

        total = page_count(pdf_path)
        if max_pages is not None:
            total = min(total, max_pages)
        if total == 0:
            raise ValueError(f"El PDF no contiene páginas: {pdf_path}")

        reference = self.reference_image
        if reference is None:
            reference = render_page(pdf_path, 1, self.config.dpi)
        logger.info(
            "[Pipeline] Referencia de alineación: "
            + ("imagen externa" if self.reference_image is not None
               else "página 1")
        )

        own_transforms, anchors = self._calibrate(pdf_path, total, reference)

        if self._is_cancelled():
            pages: List[PageResult] = []
        elif self.workers > 1:
            pages = self._process_parallel(pdf_path, total, reference,
                                           anchors, own_transforms)
        else:
            pages = self._process_sequential(pdf_path, total, reference,
                                             anchors, own_transforms)

        pages = self._verify_pages(
            pdf_path, pages, reference, anchors, own_transforms
        )

        self._notify(total, total, "Generando reporte")
        report = ValidationReport(
            pdf_path=str(pdf_path),
            template_name=self.template.name,
            pages=pages,
            cancelled=self._is_cancelled(),
            calibration_ms=self.calibration_ms,
            processing_ms=round((time.perf_counter() - t_start) * 1000, 1),
        )
        report.summary = self._compute_summary(pages)

        elapsed = time.perf_counter() - t_start
        logger.info(
            f"[Pipeline] Fin en {elapsed:.1f}s "
            f"(calibración {self.calibration_ms / 1000:.1f}s + "
            f"procesado {(elapsed * 1000 - self.calibration_ms) / 1000:.1f}s) | "
            f"{report.summary}"
            + (" | CANCELADO" if report.cancelled else "")
        )
        return report

    # ── Ejecución (secuencial / paralela) ────────────────────────────────

    def _process_sequential(
            self, pdf_path: Path, total: int, reference: np.ndarray,
            anchors: Optional[List[TransformResult]] = None,
            own: Optional[List[TransformResult]] = None,
        ) -> List[PageResult]:
        pages: List[PageResult] = []
        for page_number in range(1, total + 1):
            if self._is_cancelled():
                break
            self._notify(page_number - 1, total,
                         f"Procesando página {page_number}/{total}")
            image = render_page(pdf_path, page_number, self.config.dpi)
            pages.append(process_page_image(
                image, page_number, self.config, self.engine,
                self.template, reference,
                transform=anchors[page_number - 1] if anchors else None,
                transform_reliable=own[page_number - 1].reliable
                if own else None,
                printed_mask=self._printed_mask,
            ))
        return pages

    def _process_parallel(
            self, pdf_path: Path, total: int, reference: np.ndarray,
            anchors: Optional[List[TransformResult]] = None,
            own: Optional[List[TransformResult]] = None,
        ) -> List[PageResult]:
        pages: List[PageResult] = []
        pool = ProcessPoolExecutor(
            max_workers=self.workers,
            initializer=_init_worker,
            initargs=(
                self.config, self.template, self.engine.name,
                self.config.ocr_lang, self.cpu_threads, reference, pdf_path,
                list(anchors) if anchors else None,
                [t.reliable for t in own] if own else None,
                self._printed_mask,
            ),
        )
        try:
            futures = {
                pool.submit(_process_page_worker, page_number): page_number
                for page_number in range(1, total + 1)
            }
            consumed: set = set()
            for future in as_completed(futures):
                if self._is_cancelled():
                    break
                consumed.add(future)
                page_number = futures[future]
                page = future.result()
                pages.append((page_number, page))
                self._notify(len(pages), total,
                             f"Procesando página {page_number}/{total}")
            if self._is_cancelled():
                pool.shutdown(cancel_futures=True, wait=True)
                # Recupera las páginas ya completadas antes de cancelar.
                for future, page_number in futures.items():
                    if (future not in consumed and future.done()
                            and not future.cancelled()):
                        try:
                            pages.append((page_number, future.result()))
                        except Exception:  # noqa: BLE001 - parcial
                            continue
        finally:
            pool.shutdown(wait=True)
        pages.sort(key=lambda item: item[0])
        if self._is_cancelled():
            logger.warning(
                f"[Pipeline] Cancelado con {len(pages)}/{total} páginas"
            )
        return [page for _, page in pages]

    # ── Alineación por lote ─────────────────────────────────────────────

    def _calibrate(
        self, pdf_path: Path, total: int, reference: np.ndarray,
    ) -> Tuple[Optional[List[TransformResult]], Optional[List[TransformResult]]]:
        """Paso 1: estima la transformación de cada página a baja resolución.

        Solo renderiza + deskew + matching ORB (sin deformar ni OCR), y
        devuelve (transformaciones propias, anclas estabilizadas). Con la
        alineación deshabilitada devuelve (None, None).

        Coste: bajo (~75), ≈ 0.2-0.4 s por página — sin OCR ni warp.
        """
        t_calib = time.perf_counter()
        try:
            return self._calibrate_impl(pdf_path, total, reference)
        finally:
            self.calibration_ms = round(
                (time.perf_counter() - t_calib) * 1000, 1
            )

    def _calibrate_impl(
        self, pdf_path: Path, total: int, reference: np.ndarray,
    ) -> Tuple[Optional[List[TransformResult]], Optional[List[TransformResult]]]:
        if not self.config.align or reference is None:
            return None, None

        calib_dpi = max(75, self.config.dpi // 2)
        zoom = calib_dpi / self.config.dpi
        calib_ref = cv2.resize(reference, None, fx=zoom, fy=zoom,
                               interpolation=cv2.INTER_AREA)
        factor = self.config.dpi / calib_dpi

        own: List[TransformResult] = []
        calib_images: List[np.ndarray] = []
        for page_number in range(1, total + 1):
            if self._is_cancelled():
                break
            # Progreso como etapa, sin contar páginas: las páginas reales se
            # cuentan solo en la fase de procesamiento (done=0 no avanza).
            self._notify(
                0, total,
                f"Calibrando alineación (página {page_number}/{total})",
            )
            image = render_page(pdf_path, page_number, calib_dpi)
            if self.config.deskew:
                image, _ = deskew(image)
            tr = compute_similarity_transform(image, calib_ref, self.config)
            tr.tx *= factor
            tr.ty *= factor
            own.append(tr)

            # Guardamos las imágenes ya deskewed. La máscara se construye
            # después de estabilizar las anclas, para que use exactamente el
            # mismo marco que la fase de OCR.
            if self.config.remove_printed and total >= 3:
                calib_images.append(image)

        anchors = self._stabilize_anchors(own)
        reliable = sum(1 for t in own if t.reliable)
        logger.info(f"[Pipeline] Calibración: {reliable}/{total} páginas "
                    f"fiables; anclas estabilizadas por ventana")
        accum: Optional[np.ndarray] = None
        if calib_images:
            for image, anchor in zip(calib_images, anchors):
                calib_tr = TransformResult(
                    rot=anchor.rot, scale=anchor.scale,
                    tx=anchor.tx / factor, ty=anchor.ty / factor,
                )
                warped = warp_with_transform(
                    image, calib_tr, (calib_ref.shape[1], calib_ref.shape[0])
                )
                dark = cv2.cvtColor(
                    warped, cv2.COLOR_BGR2GRAY
                ) < self.config.printed_ink_threshold
                if accum is None:
                    accum = np.zeros_like(dark, dtype=np.float32)
                accum += dark.astype(np.float32)

        if accum is not None:
            printed = (accum / len(calib_images)) >= 0.60
            printed = cv2.dilate(
                printed.astype(np.uint8), np.ones((3, 3), np.uint8),
            )
            self._printed_mask = cv2.resize(
                printed, (reference.shape[1], reference.shape[0]),
                interpolation=cv2.INTER_NEAREST,
            ).astype(bool)
            logger.info(
                f"[Pipeline] Fondo impreso: {int(self._printed_mask.sum())}"
                " px eliminados del OCR"
            )
            if self.config.align:
                self._date_slot_map = build_slot_maps(
                    self._printed_mask, self.template
                )
                logger.info(
                    f"[Pipeline] Ranuras de casillas de fecha: "
                    f"{sorted(self._date_slot_map)}"
                )
        return own, anchors

    @staticmethod
    def _stabilize_anchors(
        transforms: List[TransformResult], half_window: int = 7
    ) -> List[TransformResult]:
        """Ancla por página = mediana de la ventana [i-half, i+half].

        La mediana es robusta a páginas catastróficas (ORB degradado) y
        deja la posición de los campos consistente entre bitácoras de un
        mismo lote. Si en la ventana no hay páginas fiables, toma la
        ventana completa como respaldo.
        """
        n = len(transforms)
        anchors: List[TransformResult] = []
        for i in range(n):
            lo = max(0, i - half_window)
            hi = min(n, i + half_window + 1)
            window = transforms[lo:hi]
            pool = [t for t in window if t.reliable] or window
            anchors.append(TransformResult(
                rot=float(np.median([t.rot for t in pool])),
                tx=float(np.median([t.tx for t in pool])),
                ty=float(np.median([t.ty for t in pool])),
                scale=float(np.median([t.scale for t in pool])),
                inliers=max((t.inliers for t in pool), default=0),
                reliable=True,
            ))
        return anchors

    # ── Pasos internos ──────────────────────────────────────────────────

    def _notify(self, done: int, total: int, message: str) -> None:
        if self.on_progress is not None:
            self.on_progress(done, total, message)

    @staticmethod
    def _compute_summary(pages: List[PageResult]) -> dict:
        summary = {
            "total_pages": len(pages),
            "ok_pages": 0,
            "warning_pages": 0,
            "error_pages": 0,
            "blank_pages": sum(1 for p in pages if p.blank),
        }
        for page in pages:
            if page.status is Status.OK:
                summary["ok_pages"] += 1
            elif page.status is Status.WARNING:
                summary["warning_pages"] += 1
            else:
                summary["error_pages"] += 1
        return summary

    # ── Verificador VLM (Fase 1: resuelve solo casos inciertos) ───────

    def _verify_pages(
        self,
        pdf_path: Path,
        pages: List[PageResult],
        reference: Optional[np.ndarray],
        anchors: Optional[List[TransformResult]],
        own: Optional[List[TransformResult]],
    ) -> List[PageResult]:
        """Arbitra con el VLM los campos incitos de la corrida.

        Corre una sola vez por PDF, en el proceso principal (el servidor
        llama-server es un subproceso único). Recorta los campos de las
        páginas que necesitan resolución, re-alineados con los mismos
        parámetros del procesado, y aplica solo respuestas terminantes.
        Si no hay binario/modelo o el servidor falla, devuelve las páginas
        tal cual (comportamiento idéntico al pre-Fase 1).
        """
        if not self.config.vlm_enabled:
            return pages
        targets = self._vlm_targets(pages)
        if not targets:
            self.vlm_stats["disabled"] = "sin casos inciertos"
            return pages

        from app.verifier.verifier import VlmVerifier

        verifier = VlmVerifier(self.config)
        if not verifier.ensure_server():
            self.vlm_stats["disabled"] = "sin binario/modelo/servidor VLM"
            return pages

        self.vlm_stats.update({
            "enabled": True,
            "targets": len(targets),
            "crops": 0,
            "signatures_resolved": 0,
            "fields_resolved": 0,
        })
        by_page: dict[int, List[Tuple[str, Optional[str]]]] = {}
        for idx, field_id, post in targets:
            by_page.setdefault(idx, []).append((field_id, post))

        for idx, items in by_page.items():
            if self._is_cancelled():
                break
            page = pages[idx]
            image = render_page(pdf_path, idx + 1, self.config.dpi)
            image = self._aligned_image(
                image, idx, reference, anchors=anchors, own=own
            )
            date_touched = False
            for field_id, post in items:
                field_tmpl = self.template.field(field_id)
                if field_tmpl is None:
                    continue
                try:
                    crop = crop_region(
                        image, field_tmpl, self.config.crop_padding
                    )
                except ValueError:
                    continue
                if self.config.crop_preprocess and (
                    field_tmpl.localize == "ink"
                    and field_tmpl.postprocess not in _DATE_FIELDS
                ):
                    localized = crop_to_ink(crop, dpi=self.config.dpi)
                    if localized is not None:
                        crop = localized
                field = next(
                    (f for f in page.fields if f.field_id == field_id),
                    None,
                )
                if field is None:
                    continue
                if post == "signature":
                    verdict = verifier.check_signature(crop)
                    if verdict is not None:
                        self._apply_signature_verdict(field, field_tmpl, verdict)
                        self.vlm_stats["crops"] += 1
                        self.vlm_stats["signatures_resolved"] += 1
                else:
                    read_kind = (
                        "matricula" if post == "matricula"
                        else "month" if post == "month" else "digits"
                    )
                    token = verifier.read_text(crop, read_kind)
                    if token:
                        refined, note = apply_postprocess(
                            field_id, field_tmpl.postprocess, token
                        )
                        if refined:
                            self._apply_text_verdict(
                                field, field_tmpl, refined, note
                            )
                            self.vlm_stats["crops"] += 1
                            self.vlm_stats["fields_resolved"] += 1
                            if field_id in ("day", "month", "year"):
                                date_touched = True
            if date_touched:
                _combine_date_parts(page)
            validate_page(page, self.template, self.config)
        self.vlm_stats["crops"] = min(
            self.vlm_stats["crops"], self.config.vlm_max_crops)
        logger.info(f"[VLM] {self.vlm_stats}")
        return pages

    def _vlm_targets(
        self, pages: List[PageResult]
    ) -> List[Tuple[int, str, Optional[str]]]:
        """Campos que merecen arbitraje: firma 'unclear' o crítico vacío."""
        targets: List[Tuple[int, str, Optional[str]]] = []
        for idx, page in enumerate(pages):
            if page.blank:
                continue
            for field in page.fields:
                if len(targets) >= self.config.vlm_max_crops:
                    return targets
                tmpl = self.template.field(field.field_id)
                if tmpl is None:
                    continue
                if tmpl.type is FieldType.SIGNATURE:
                    if field.value == UNCLEAR:
                        targets.append((idx, field.field_id, "signature"))
                elif (
                    tmpl.postprocess in _CRITICAL_POSTPROCESS
                    and not field.value
                    and field.status is not Status.OK
                ):
                    targets.append(
                        (idx, field.field_id, tmpl.postprocess)
                    )
        return targets

    def _aligned_image(
        self,
        image: np.ndarray,
        index: int,
        reference: Optional[np.ndarray],
        anchors: Optional[List[TransformResult]] = None,
        own: Optional[List[TransformResult]] = None,
    ) -> np.ndarray:
        """Reproduce deskew + alineación + máscara impresa del procesado."""
        if self.config.deskew:
            image, _skew = deskew(image)
        if self.config.align and reference is not None:
            if anchors and index < len(anchors) and anchors[index] is not None:
                image = apply_transform(image, anchors[index])
            else:
                own_t = compute_similarity_transform(image, reference, self.config)
                image = apply_transform(image, own_t)
        if self._printed_mask is not None:
            mask = self._printed_mask
            if mask.shape != image.shape[:2]:
                mask = cv2.resize(
                    mask.astype(np.uint8),
                    (image.shape[1], image.shape[0]),
                    interpolation=cv2.INTER_NEAREST,
                ).astype(bool)
            image[mask] = 255
        return image

    @staticmethod
    def _apply_signature_verdict(
        field: "FieldResult", tmpl, verdict: bool
    ) -> None:
        if verdict:
            field.value = "true"
            field.confidence = 0.90
            field.status = Status.OK
            field.comment = "Firma verificada por VLM (PRESENTE)"
        else:
            field.value = "false"
            field.confidence = 0.90
            field.status = (
                Status.ERROR if tmpl.required else Status.WARNING
            )
            field.comment = "Firma verificada por VLM (AUSENTE)"

    @staticmethod
    def _apply_text_verdict(
        field: FieldResult, tmpl, value: str, note: str
    ) -> None:
        field.value = value
        field.confidence = max(field.confidence, 0.80)
        field.comment = "Verificado por VLM" if not note else note
        field.status = (
            Status.OK
            if not note or note in _SOFT_NOTES
            else Status.ERROR
        )

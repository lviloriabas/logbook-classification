"""Orquestador del pipeline de validación de bitácoras.

Flujo por página:
    render → blank → deskew → alineación → recorte por región
    → OCR/firma/checkbox (OCR en lote por página) → postproceso
    → reglas de validación.

El pipeline depende de interfaces (OcrEngine, Template), por lo que se
pueden añadir motores OCR, nuevos tipos de campo o paralelismo sin
modificar esta clase.

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
from app.ocr.date_ocr import decode_slots, ocr_fallback, read_date_slots
from app.ocr.regional import ocr_regions
from app.templates.schema import FieldType, Template
from app.utils.postprocess import (
    _OCR_LETTER_TO_DIGIT_DICT,
    MONTH_WORDS,
    NUMERIC_MONTH_NOTE,
    WEAK_MATRICULA_NOTE,
    apply_postprocess,
    combine_date,
)
from app.validation.validator import validate_page
from app.vision.alignment import (
    TransformResult,
    apply_transform,
    compute_similarity_transform,
    scale_transform_for_shape,
    warp_with_transform,
)
from app.vision.blank_detection import is_blank
from app.vision.checkbox import detect_checkbox
from app.vision.ink_extent import crop_to_ink, strip_date_label
from app.vision.pdf_loader import page_count, render_page
from app.vision.preprocessing import crop_region, deskew, rotate, upscale_for_ocr
from app.vision.signature import UNCLEAR, detect_signature
from app.vision.date_geometry import (
    DateFieldGeometry,
    field_override,
    locate_date_grid,
)
from app.vision.date_slots import (
    build_slot_maps,
    crop_slots,
    localize_slot,
    scale_slot_map,
)

ProgressCallback = Callable[[int, int, str], None]

_ORDER = {Status.OK: 0, Status.WARNING: 1, Status.ERROR: 2}

# Notas de postproceso "blandas": no son fallos del campo, solo avisos.
_SOFT_NOTES = (
    WEAK_MATRICULA_NOTE,
)

_WORKER_STATE: dict = {}


def _init_worker(config: AppConfig, template: Template, engine_name: str,
                 lang: str, cpu_threads: Optional[int],
                 reference: Optional[np.ndarray], pdf_path: Path,
                 transforms: Optional[List[TransformResult]] = None,
                 own_reliability: Optional[List[bool]] = None,
                 printed_mask: Optional[np.ndarray] = None,
                 slot_map: Optional[dict] = None,
                 date_engine_name: Optional[str] = None) -> None:
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
    if date_engine_name and date_engine_name != engine_name:
        _WORKER_STATE["date_engine"] = create_engine(
            date_engine_name, lang=lang, **kwargs
        )


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
        transform_reliable=(
            transforms[page_number - 1].reliable
            if len(transforms) >= page_number
            else reliability[page_number - 1]
            if len(reliability) >= page_number else None
        ),
        printed_mask=_WORKER_STATE.get("printed_mask"),
        slot_map=_WORKER_STATE.get("slot_map"),
        date_engine=_WORKER_STATE.get("date_engine"),
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
    date_engine: Optional[OcrEngine] = None,
) -> PageResult:
    """Renderiza y procesa una página del PDF (función de proceso worker)."""
    image = render_page(pdf_path, page_number, config.dpi)
    date_image: Optional[np.ndarray] = None
    if config.date_dpi > config.dpi:
        date_image = render_page(pdf_path, page_number, config.date_dpi)
    return process_page_image(image, page_number, config, engine, template,
                              reference, transform, transform_reliable,
                              printed_mask, slot_map, date_engine=date_engine,
                              date_image=date_image)


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
    date_engine: Optional[OcrEngine] = None,
    date_image: Optional[np.ndarray] = None,
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
    deskew_angle = 0.0
    if config.deskew:
        image, deskew_angle = deskew(image)
        page.skew_angle = round(deskew_angle, 3)
        if date_image is not None and abs(deskew_angle) > 0.0:
            # La imagen de fecha se renderiza a otro DPI, pero debe conservar
            # exactamente el mismo ángulo que la página principal.
            date_image = rotate(date_image, deskew_angle)
        if abs(deskew_angle) > 0.05:
            logger.info(
                f"[Página {page_number}] Deskew: {deskew_angle:.2f}°"
            )

    # 3) Alineación con la plantilla (ancla estabilizada por lote si existe)
    alignment_transform: Optional[TransformResult] = None
    if config.align and reference is not None:
        if transform is not None:
            reliable = (
                transform_reliable
                if transform_reliable is not None
                else transform.reliable
            )
            if reliable:
                alignment_transform = transform
            quality = "ok" if reliable else "low"
        else:
            own = compute_similarity_transform(image, reference, config)
            if own.reliable:
                alignment_transform = own
            quality = "ok" if own.reliable else "low"
        if alignment_transform is not None:
            image = apply_transform(image, alignment_transform)
            if date_image is not None:
                date_transform = scale_transform_for_shape(
                    alignment_transform, image.shape, date_image.shape
                )
                date_image = apply_transform(date_image, date_transform)
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

    # 3.7) Geometría dinámica de la banda de fecha: ajusta las casillas a
    # la retícula impresa de esta página (traslación/escala pequeñas). Si
    # la retícula no encaja, queda vacío y se usa la plantilla.
    date_geometries: dict[str, DateFieldGeometry] = {}
    date_overrides: dict = {}
    if config.date_dynamic_geometry:
        geom_source = date_image if date_image is not None else image
        date_geometries = locate_date_grid(
            geom_source, template, config.printed_ink_threshold
        )
        for field_id, geometry in date_geometries.items():
            base_field = template.field(field_id)
            if base_field is not None:
                date_overrides[field_id] = field_override(
                    geometry, base_field, geom_source.shape
                )

    # Las siete celdas manuscritas heredan la retícula encontrada. Así el
    # OCR por carácter no usa rectángulos estáticos que puedan incluir el
    # rótulo impreso o cortar un trazo cuando la página se desplazó.
    char_overrides = _date_cell_overrides(
        template, date_geometries, geom_source.shape
        if config.date_dynamic_geometry else image.shape,
    )
    ocr_overrides = {**date_overrides, **char_overrides}

    # 4-7) Campos: recorte → OCR (en lote)/firma/checkbox → postproceso
    ocr_fields = [
        field for field in template.fields
        if field.type not in (FieldType.SIGNATURE, FieldType.CHECKBOX)
    ]
    ocr_results = ocr_regions(engine, image, ocr_fields,
                              config.crop_padding,
                              preprocess=config.crop_preprocess,
                              dpi=config.dpi,
                              date_engine=date_engine,
                              date_image=date_image,
                              overrides=ocr_overrides)
    by_id = {field.id: result
             for field, result in zip(ocr_fields, ocr_results)}

    for field in template.fields:
        if field.type in (FieldType.SIGNATURE, FieldType.CHECKBOX):
            region = crop_region(analysis_image, field, config.crop_padding)
            if field.type is FieldType.SIGNATURE:
                result = detect_signature(region, field, page_number)
                if page.alignment_quality != "ok":
                    result.status = Status.WARNING
                    result.confidence = min(result.confidence, 0.40)
                    result.inference_method = "alignment_low"
                    result.comment = (
                        f"{result.comment} | Alineación no confiable; "
                        "firma requiere revisión"
                    )
            else:
                result = detect_checkbox(region, field, page_number)
        else:
            raw_text, confidence = by_id.get(field.id, ("", 0.0))
            text, note = raw_text, ""
            recovered = False
            recovered_via = ""
            alternatives: List[str] = []
            is_date_field = field.postprocess in _DATE_FIELDS
            field_image = (
                date_image
                if is_date_field and date_image is not None
                else image
            )
            field_dpi = (
                config.date_dpi
                if is_date_field and date_image is not None
                else config.dpi
            )
            # Geometría ajustada por la retícula impresa de esta página.
            crop_field = (
                date_overrides.get(field.id, field)
                if is_date_field else field
            )
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
                    crop_field, field_image, config.crop_padding,
                    preprocess=config.crop_preprocess,
                    dpi=field_dpi,
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
            # con restricciones (dígitos / abreviatura de mes). Se prefiere
            # el mapa de la retícula detectada en esta página; si no, el
            # mapa global del fondo impreso.
            page_spec = None
            geometry = date_geometries.get(field.id)
            if geometry is not None:
                page_spec = geometry.slot_spec()
            elif slot_map is not None and field.id in slot_map:
                page_spec = slot_map[field.id]
                if field_image is not image:
                    page_spec = scale_slot_map(
                        page_spec, image.shape, field_image.shape
                    )
            if (
                config.date_slot_ocr
                and is_date_field
                and page_spec is not None
            ):
                fb = _slot_ocr_fallback(
                    crop_field, field_image, config.crop_padding, page_spec,
                    preprocess=config.crop_preprocess,
                    dpi=field_dpi,
                    engine=(
                        date_engine if date_engine is not None else engine
                    ),
                )
                if fb is not None:
                    fb_text, fb_conf = fb
                    candidate_text, fb_note = apply_postprocess(
                        field.id, field.postprocess, fb_text
                    )
                    if candidate_text and _accept_slot_candidate(
                        field.postprocess, text, confidence,
                        fb_text, fb_conf,
                    ):
                        if text and text != candidate_text:
                            alternatives.append(text)
                        text = candidate_text
                        confidence = fb_conf
                        note = fb_note
                        recovered = True
                        recovered_via = "ranuras"
                        logger.info(
                            f"[Página {page_number}] {field.id}: "
                            f"valor recuperado por ranuras de casilla "
                            f"({fb_text!r})"
                        )
                    elif candidate_text and candidate_text != text:
                        # Candidato válido pero no aceptado: queda como
                        # alternativa para el consenso por libro.
                        alternatives.append(candidate_text)
            status = Status.OK
            if note and not any(n in note for n in _SOFT_NOTES):
                status = Status.ERROR
            if NUMERIC_MONTH_NOTE in note:
                # El valor numérico solo queda ratificado después, si las
                # celdas posicionales contienen exclusivamente esos dígitos.
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
                source="ocr_fallback" if recovered else "ocr",
                inference_method=recovered_via or None,
                alternatives=alternatives,
            )
            if recovered and not note:
                result.comment = (
                    f"OCR respaldo ({recovered_via}): {fb_text!r}"
                    if recovered_via else
                    f"OCR respaldo (tesseract): {fb_text!r}"
                )
        page.add_field(result)

    # 7a) Unir las celdas de carácter (day_1..year_2) en day/month/year
    _join_char_fields(page)

    # 7b) Combinar day/month/year en una sola fecha normalizada
    _combine_date_parts(page)

    # 8) Reglas de validación (required/regex/longitud/confianza)
    validate_page(page, template, config)
    if page.alignment_quality != "ok":
        if page.status is Status.OK:
            page.status = Status.WARNING
        page.comment = "Alineación no confiable; revisar campos críticos"
    logger.info(f"[Página {page_number}] Estado: {page.status.value}")

    page.processing_ms = round((time.perf_counter() - t_start) * 1000, 1)
    return page


_CRITICAL_POSTPROCESS = frozenset(
    {"day", "month", "year", "matricula", "digits"}
)
_TIGHT_CROP_POSTPROCESS = _CRITICAL_POSTPROCESS

# Campos de fecha: se leen sin crop_to_ink (ver _critical_ocr_fallback).
_DATE_FIELDS = frozenset({"day", "month", "year"})

# Celdas de carácter de la banda de fecha (una por posición de casilla).
_CHAR_COMPONENTS = (("day", 2), ("month", 3), ("year", 2))


def _date_cell_overrides(
    template: Template,
    geometries: dict[str, DateFieldGeometry],
    image_shape: Tuple[int, ...],
) -> dict[str, "FieldTemplate"]:
    """Deriva los siete recortes de carácter desde la retícula detectada."""
    height, width = image_shape[:2]
    result: dict[str, "FieldTemplate"] = {}
    for component, count in _CHAR_COMPONENTS:
        geometry = geometries.get(component)
        if geometry is None:
            continue
        boundaries = geometry.slot_spec()["boundaries"]
        if len(boundaries) != count + 1:
            continue
        _left, top, _right, bottom = geometry.rect
        for index, (left, right) in enumerate(
            zip(boundaries, boundaries[1:]), start=1
        ):
            cell = template.field(f"{component}_{index}")
            if cell is None or right <= left:
                continue
            result[cell.id] = cell.model_copy(update={
                "x": left / width,
                "y": top / height,
                "w": (right - left) / width,
                "h": (bottom - top) / height,
            })
    return result
def _accept_slot_candidate(
    rule: Optional[str],
    current_text: str,
    current_confidence: float,
    candidate_raw: str,
    candidate_confidence: float,
) -> bool:
    """Arbitra a favor de una lectura posicional suficientemente legible.

    La fecha siempre es manuscrita y cada carácter tiene una ranura física.
    Por eso la confianza del OCR global no debe bloquear un valor completo
    leído en las posiciones correctas: las confianzas de ambos recortes no
    son comparables. Los umbrales solo impiden que una ranura casi vacía
    reemplace una lectura válida.
    """
    if not candidate_raw:
        return False
    minimum = 0.35 if current_text and candidate_raw != current_text else 0.25
    if candidate_confidence < minimum:
        return False
    if not current_text:
        return True
    if candidate_raw == current_text:
        return True
    if rule in _DATE_FIELDS:
        return True
    return candidate_confidence >= current_confidence


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
    engine: Optional[OcrEngine] = None,
) -> Optional[Tuple[str, float]]:
    """Tercera pasada: OCR por carácter sobre las ranuras de la casilla.

    La casilla lleva separadores verticales impresos entre caracteres;
    con el mapa de ranuras (posiciones fijas derivadas del fondo impreso)
    se recorta cada celda y se lee con Tesseract PSM 10 + whitelist,
    decodificando con restricciones (dígitos o abreviatura de mes).
    ``preprocess=False`` lee las ranuras crudas. ``engine`` es el motor
    genérico (p. ej. PaddleOCR) que se usa cuando Tesseract no está
    disponible; las restricciones por regla las aplica
    ``read_date_slots``.
    """
    slots = crop_slots(image, field, 0.0, slot_spec)
    if not slots:
        return None
    if preprocess:
        slots = [localize_slot(slot, dpi=dpi) for slot in slots]
    return read_date_slots(field.id, field.postprocess, slots,
                           preprocess=preprocess, engine=engine)


def _join_char_fields(page: PageResult) -> None:
    """Reconstruye day/month/year a partir de las celdas de carácter.

    Cada casilla de la banda de fecha (day_1..year_2) está separada de
    sus vecinas por una barra vertical impresa; el OCR del campo
    completo puede perder caracteres o fusionar la barra con el trazo.
    Las celdas de carácter de la plantilla no sufren por la barra y
    localizan la tinta dentro de su propia casilla (localize='ink').

    Si las celdas unidas forman un valor canónico (aplicando el
    postprocesador del componente), la evidencia posicional reemplaza el
    OCR global incluso cuando este último reportó confianza alta.
    """
    by_id = {result.field_id: result for result in page.fields}
    for component, count in _CHAR_COMPONENTS:
        target = by_id.get(component)
        if target is None:
            continue
        cells: List[FieldResult] = []
        for index in range(1, count + 1):
            cell = by_id.get(f"{component}_{index}")
            if cell is None:
                cells.clear()
                break
            cells.append(cell)
        if not cells:
            continue
        numeric_ranked: List[Tuple[float, str]] = []
        if component in ("day", "year"):
            per_cell = [
                _numeric_cell_candidates(cell, component, index)
                for index, cell in enumerate(cells)
            ]
            if all(per_cell):
                numeric_ranked = sorted(
                    (
                        (round((left_score + right_score) / 2.0, 3), left + right)
                        for left, left_score in per_cell[0].items()
                        for right, right_score in per_cell[1].items()
                        if component == "year" or 1 <= int(left + right) <= 31
                    ),
                    reverse=True,
                )
            aligned = [
                max(options, key=options.get) if options else ""
                for options in per_cell
            ]
        else:
            aligned = [
                _month_cell(_date_cell_observation(cell), index)
                for index, cell in enumerate(cells)
            ]
            # El recorte completo suele conservar una letra que la celda
            # pierde (o viceversa). Se fusionan por posición antes de elegir
            # entre las 12 abreviaturas válidas.
            global_month = re.sub(
                r"[^A-Za-z0-9/]", "", target.raw_value or ""
            )
            global_aligned = (
                [
                    _month_cell(character, index)
                    for index, character in enumerate(global_month)
                ]
                if len(global_month) == 3 else ["", "", ""]
            )
            month_scores: List[float] = []
            for index in range(3):
                if not aligned[index] and global_aligned[index]:
                    aligned[index] = global_aligned[index]
                scores = []
                if _month_cell(_date_cell_observation(cells[index]), index):
                    scores.append(cells[index].confidence)
                if global_aligned[index] == aligned[index]:
                    scores.append(target.confidence)
                month_scores.append(max(scores, default=0.0))

        # Evidencia estructurada completa. Para día también se acepta un solo
        # dígito únicamente si está en la segunda ranura (alineado a la
        # derecha); un dígito solo en la primera indica normalmente que el
        # segundo se perdió durante el OCR.
        if component == "day":
            if all(aligned):
                raw = "".join(aligned)
            elif (
                not (cells[0].raw_value or cells[0].value)
                and not aligned[0]
                and aligned[1]
            ):
                raw = aligned[1]
            else:
                raw = ""
        elif not all(aligned):
            if component == "month":
                readings = [
                    (_date_cell_observation(cell), cell.confidence)
                    for cell in cells
                ]
                partial = decode_slots("month", readings)
                if partial[0]:
                    raw = partial[0]
                else:
                    raw = _numeric_month_cells(cells)
            else:
                raw = ""
        else:
            raw = "".join(aligned)
        if numeric_ranked:
            structured_confidence, raw = numeric_ranked[0]
            for _score, candidate_value in numeric_ranked[1:]:
                if (
                    candidate_value != raw
                    and candidate_value not in target.alternatives
                ):
                    target.alternatives.append(candidate_value)
        if not raw:
            if not target.value and any(cell.raw_value or cell.value for cell in cells):
                target.value = None
                target.status = Status.ERROR
                target.comment = "Incomplete or invalid handwritten date cells"
            continue
        candidate, note = apply_postprocess(component, component, raw)
        numeric_month = component == "month" and NUMERIC_MONTH_NOTE in note
        if not candidate or (note and not numeric_month):
            continue
        if numeric_month:
            # Un mes numérico global solo se confirma si coincide con los
            # dígitos observados dentro de la retícula.
            global_numeric = re.sub(r"\D", "", target.value or "")
            if global_numeric and int(global_numeric) != int(candidate):
                continue
        if not numeric_ranked:
            confidences = (
                [score for score in month_scores if score > 0]
                if component == "month" else
                [
                    cell.confidence for cell in cells
                    if _date_cell_observation(cell)
                ]
            )
            structured_confidence = (
                round(sum(confidences) / len(confidences), 3)
                if confidences else 0.0
            )
        minimum_structured_confidence = (
            0.18 if component == "month" and raw in {
                word for word, _number in MONTH_WORDS
            } else 0.35
        )
        if structured_confidence < minimum_structured_confidence:
            continue
        # Una lectura completa por posiciones vence una lectura global
        # discrepante. La confianza de los motores no es comparable entre
        # recortes completos y celdas, por lo que no se usa como único árbitro.
        if (
            target.value == candidate
            and target.status is Status.OK
            and not numeric_month
        ):
            continue
        previous = target.value
        conflict_threshold = 0.55 if component == "month" else 0.65
        if (
            previous
            and previous != candidate
            and target.status is not Status.ERROR
            and structured_confidence < conflict_threshold
        ):
            if candidate not in target.alternatives:
                target.alternatives.append(candidate)
            if target.confidence < 0.5:
                if previous not in target.alternatives:
                    target.alternatives.append(previous)
                target.value = None
                target.status = Status.ERROR
                target.comment = "Conflicting weak handwritten date readings"
            continue
        if previous and previous != candidate and previous not in target.alternatives:
            target.alternatives.append(previous)
        target.value = candidate
        target.raw_value = raw
        target.confidence = structured_confidence
        target.status = Status.WARNING if numeric_month else Status.OK
        target.comment = (
            f"{NUMERIC_MONTH_NOTE} confirmado por celdas: {raw!r}"
            if numeric_month else f"unido de celdas: {raw!r}"
        )
        target.source = "date_cells"
        target.inference_method = "date_cells"
        logger.info(
            f"[Página {page.page_number}] {component}: "
            f"reconstruido por celdas de carácter ({raw!r})"
        )

    # Un día de un dígito leído en el campo completo es ambiguo: puede ser un
    # día real o el primer carácter de un día de dos cifras. Solo se conserva
    # si proviene de ranuras (posición física conocida) o de celdas validadas.
    day = by_id.get("day")
    if (
        day is not None
        and re.fullmatch(r"\d", day.value or "")
        and day.inference_method not in {"ranuras", "date_cells"}
    ):
        ambiguous = day.value
        if ambiguous not in day.alternatives:
            day.alternatives.append(ambiguous)
        day.value = None
        day.status = Status.ERROR
        day.comment = "Ambiguous single-digit day without positional evidence"


def _numeric_cell(value: Optional[str]) -> str:
    """Normaliza un único carácter manuscrito de una ranura numérica."""
    if not value or len(value) != 1:
        return ""
    if value.isdigit():
        return value
    return _OCR_LETTER_TO_DIGIT_DICT.get(value, "")


def _date_cell_observation(cell: FieldResult) -> str:
    """Conserva la evidencia cruda de una celda aunque no sea ASCII."""
    return (cell.raw_value or cell.value or "").strip()


def _numeric_cell_candidates(
    cell: FieldResult, component: str, position: int,
) -> dict[str, float]:
    """Candidatos de un dígito manuscrito con peso visual.

    PP-OCRv5 es multilingüe y algunos ``2`` manuscritos se decodifican como
    ideogramas simples; ``C/B`` aparecen a menudo para un ``6`` abierto. Se
    conservan ambas interpretaciones cuando son realmente ambiguas, en vez
    de reemplazar globalmente un carácter.
    """
    observed = _date_cell_observation(cell)
    if not observed:
        return {}
    weights: dict[str, float] = {}
    base_confidence = max(0.05, float(cell.confidence))
    ambiguous = {
        "O": (("0", .90),), "Q": (("0", .80),),
        "I": (("1", .85),), "L": (("1", .70),),
        "Z": (("2", .90),), "乙": (("2", .85),), "二": (("2", .80),),
        "S": (("5", .85),),
        "G": (("6", .85),), "C": (("6", .72), ("0", .40)),
        "B": (("8", .82), ("6", .68)),
    }
    for character in observed.upper():
        if character.isascii() and character.isdigit():
            possibilities = ((character, 1.0),)
            # Un cero muy abierto puede ser leído como 4 en el año.
            if component == "year" and position == 1 and character == "4":
                possibilities += (("0", .35),)
        else:
            possibilities = ambiguous.get(character, ())
        for digit, visual_weight in possibilities:
            score = base_confidence * visual_weight
            weights[digit] = max(weights.get(digit, 0.0), score)
    return weights


def _month_cell(value: Optional[str], position: int) -> str:
    """Normaliza confusiones visuales de una ranura de mes."""
    if not value or len(value) != 1:
        return ""
    mappings = (
        {"3": "J", "I": "J", "/": "J"},
        {"0": "U", "V": "U"},
        {"1": "L", "I": "L", "C": "L", "2": "L"},
    )
    mapping = mappings[position] if 0 <= position < len(mappings) else {}
    normalized = mapping.get(value.upper(), value.upper())
    return normalized if re.fullmatch(r"[A-Z]", normalized) else ""


def _numeric_month_cells(cells: List[FieldResult]) -> str:
    """Mes 1-12 solo si toda tinta observada en las ranuras es numérica."""
    digits: List[str] = []
    for cell in cells:
        observed = cell.raw_value or cell.value or ""
        if not observed:
            continue
        digit = _numeric_cell(cell.value)
        if not digit:
            return ""
        digits.append(digit)
    raw = "".join(digits)
    if not raw or len(raw) > 2:
        return ""
    return raw if 1 <= int(raw) <= 12 else ""


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
    if any(part.status is Status.ERROR for part in (day, month, year)):
        page.date = None
        logger.debug(
            f"[Página {page.page_number}] Fecha no combinada: "
            "hay un componente inválido"
        )
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
        date_engine: Optional[OcrEngine] = None,
    ) -> None:
        self.config = config
        self.engine = engine
        self.date_engine = date_engine
        self.template = template
        self.reference_image = reference_image
        self.on_progress = on_progress
        self.workers = max(1, workers)
        self.cpu_threads = cpu_threads
        self._printed_mask: Optional[np.ndarray] = None
        self._date_slot_map: dict = {}
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
            date_image: Optional[np.ndarray] = None
            if self.config.date_dpi > self.config.dpi:
                date_image = render_page(pdf_path, page_number, self.config.date_dpi)
            pages.append(process_page_image(
                image, page_number, self.config, self.engine,
                self.template, reference,
                transform=anchors[page_number - 1] if anchors else None,
                transform_reliable=(
                    anchors[page_number - 1].reliable
                    if anchors else own[page_number - 1].reliable
                    if own else None
                ),
                printed_mask=self._printed_mask,
                slot_map=self._date_slot_map,
                date_engine=self.date_engine,
                date_image=date_image,
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
                self._date_slot_map,
                self.date_engine.name if self.date_engine else None,
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
        self._printed_mask = None
        self._date_slot_map = {}
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
        aligned_count = 0
        if calib_images:
            for image, anchor in zip(calib_images, anchors):
                if not anchor.reliable:
                    continue
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
                aligned_count += 1

        if accum is not None and aligned_count:
            printed = (accum / aligned_count) >= 0.60
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
        if self.config.date_slot_ocr:
            slot_mask = self._printed_mask
            if slot_mask is None:
                # Sin consenso de fondo impreso se usa la division uniforme
                # de la plantilla para no desactivar silenciosamente el
                # fallback por ranuras.
                slot_mask = np.zeros(reference.shape[:2], dtype=bool)
            self._date_slot_map = build_slot_maps(slot_mask, self.template)
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
            reliable_pool = [t for t in window if t.reliable]
            pool = reliable_pool or window
            anchors.append(TransformResult(
                rot=float(np.median([t.rot for t in pool])),
                tx=float(np.median([t.tx for t in pool])),
                ty=float(np.median([t.ty for t in pool])),
                scale=float(np.median([t.scale for t in pool])),
                inliers=max((t.inliers for t in pool), default=0),
                reliable=bool(reliable_pool),
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
            self.vlm_stats["model"] = getattr(verifier, "model_name", None)
            self.vlm_stats["disabled"] = "sin binario/modelo/servidor VLM"
            return pages

        self.vlm_stats.update({
            "enabled": True,
            "model": getattr(verifier, "model_name", None),
            "targets": len(targets),
            "date_targets": sum(
                1 for _, _, post in targets if post in _DATE_FIELDS
            ),
            "crops": 0,
            "signatures_resolved": 0,
            "fields_resolved": 0,
            "date_fields_resolved": 0,
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
                    field_padding = (
                        0.024
                        if field_tmpl.postprocess in _DATE_FIELDS
                        else self.config.crop_padding
                    )
                    crop = crop_region(
                        image, field_tmpl, field_padding
                    )
                except ValueError:
                    continue
                if field_tmpl.postprocess in _DATE_FIELDS:
                    # The date labels share the handwriting crop and can be
                    # mistaken for characters by a multimodal model too.
                    crop = strip_date_label(crop)
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
                        else post if post in {"day", "month", "year"}
                        else "digits"
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
                                self.vlm_stats["date_fields_resolved"] += 1
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
        """Selecciona todas las fechas y luego otros campos inciertos.

        La fecha es responsabilidad del VLM cuando está disponible, incluso
        si el OCR previo produjo un valor plausible. Se priorizan las fechas
        para que el presupuesto de recortes no se consuma antes de revisar
        todas las páginas.
        """
        date_targets: List[Tuple[int, str, Optional[str]]] = []
        other_targets: List[Tuple[int, str, Optional[str]]] = []
        for idx, page in enumerate(pages):
            if page.blank:
                continue
            for field in page.fields:
                tmpl = self.template.field(field.field_id)
                if tmpl is None:
                    continue
                if tmpl.postprocess in _DATE_FIELDS:
                    date_targets.append((idx, field.field_id, tmpl.postprocess))
                elif tmpl.type is FieldType.SIGNATURE:
                    if field.value == UNCLEAR:
                        other_targets.append((idx, field.field_id, "signature"))
                elif tmpl.postprocess in _CRITICAL_POSTPROCESS:
                    if field.status is not Status.OK and not field.value:
                        other_targets.append(
                            (idx, field.field_id, tmpl.postprocess)
                        )
        return (date_targets + other_targets)[:self.config.vlm_max_crops]

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
                if anchors[index].reliable:
                    image = apply_transform(image, anchors[index])
            else:
                own_t = compute_similarity_transform(image, reference, self.config)
                if own_t.reliable:
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
            field.source = "vlm"
            field.inference_method = "vlm_signature_review"
            field.comment = "Firma verificada por VLM (PRESENTE)"
        else:
            field.value = "false"
            field.confidence = 0.90
            field.status = (
                Status.ERROR if tmpl.required else Status.WARNING
            )
            field.source = "vlm"
            field.inference_method = "vlm_signature_review"
            field.comment = "Firma verificada por VLM (AUSENTE)"

    @staticmethod
    def _apply_text_verdict(
        field: FieldResult, tmpl, value: str, note: str
    ) -> None:
        field.value = value
        field.confidence = max(field.confidence, 0.80)
        field.source = "vlm"
        field.inference_method = "vlm_text_review"
        field.comment = "Verificado por VLM" if not note else note
        field.status = (
            Status.OK
            if not note or note in _SOFT_NOTES
            else Status.ERROR
        )

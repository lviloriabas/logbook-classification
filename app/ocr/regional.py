"""OCR aplicado únicamente sobre regiones definidas por la plantilla."""

from __future__ import annotations

from typing import List, Optional, Tuple

import cv2
import numpy as np
from loguru import logger

from app.models.schemas import OcrResult
from app.ocr.engine import OcrEngine
from app.templates.schema import FieldTemplate
from app.vision.ink_extent import crop_to_ink, strip_date_label
from app.vision.pdf_loader import RenderedRegion
from app.vision.preprocessing import crop_region, upscale_for_ocr

_DATE_FIELDS = frozenset({"day", "month", "year"})
_TIGHT_FIELDS = frozenset({"day", "month", "year", "matricula", "digits"})

_CLAHE = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))


def _preprocess_date_region(region: np.ndarray) -> np.ndarray:
    """Preprocesa una región de fecha (day/month/year) para OCR.

    CLAHE mejora el contraste local. Los formularios tienen tinta oscura
    sobre papel claro, por lo que se conserva siempre esa polaridad. Los
    bordes impresos de la casilla no se pueden usar para inferir polaridad:
    al ser oscuros hacían que recortes equivalentes se invirtieran de forma
    distinta y algunos llegaran al OCR como texto blanco sobre fondo negro.
    """
    gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY) if region.ndim == 3 else region
    enhanced = _CLAHE.apply(gray)

    return cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR)


def ocr_regions(
    engine: OcrEngine,
    page: np.ndarray,
    fields: List[FieldTemplate],
    crop_padding: float = 0.01,
    preprocess: bool = True,
    dpi: Optional[int] = None,
    date_engine: Optional[OcrEngine] = None,
    date_image: Optional[np.ndarray] = None,
    date_region: Optional[RenderedRegion] = None,
    overrides: Optional[dict[str, FieldTemplate]] = None,
) -> List[Tuple[str, float]]:
    """Aplica OCR a varias regiones en una sola llamada al motor.

    Args:
        engine: Motor OCR principal (campos no-fecha).
        page: Página alineada (BGR) a la resolución principal.
        fields: Campos con coordenadas relativas.
        crop_padding: Margen relativo de los recortes.
        preprocess: Si True localiza la tinta y reescala el recorte.
        dpi: DPI del render para escalar umbrales de localización.
        date_engine: Motor OCR para campos de fecha (day/month/year).
            Si es None, los campos de fecha usan ``engine``.
        date_image: Página completa a mayor resolución (compatibilidad).
        date_region: Banda alineada renderizada a mayor resolución. Tiene
            prioridad sobre ``date_image`` y evita rasterizar la página entera.
        overrides: Geometría ajustada por página (``locate_date_grid``);
            solo afecta al recorte, no al ``id`` ni a las reglas del campo.

    Returns:
        Lista (mismo orden que ``fields``) de (texto unido, confianza 0-1).
    """
    engines: dict[Optional[str], list[tuple[int, np.ndarray]]] = {}
    positions: dict[Optional[str], list[int]] = {}
    # Se preasigna para conservar exactamente el orden de ``fields`` aun
    # cuando se usan motores distintos o un recorte individual falla.
    results: List[Tuple[str, float]] = [("", 0.0) for _ in fields]

    for index, field in enumerate(fields):
        try:
            crop_field = (overrides or {}).get(field.id, field)
            is_date = (
                field.postprocess in _DATE_FIELDS
                or field.postprocess == "char"
            )
            if is_date and date_region is not None:
                source = date_region.image
                crop_field = date_region.local_field(crop_field)
            else:
                source = (
                    date_image
                    if is_date and date_image is not None
                    else page
                )
            field_padding = (
                0.0 if field.postprocess == "char" else
                0.024 if is_date else
                0.0 if field.postprocess in _TIGHT_FIELDS else
                crop_padding
            )
            region = crop_region(source, crop_field, pad=field_padding)
        except ValueError as exc:
            logger.warning(f"OCR {field.id}: {exc}")
            continue

        if preprocess:
            if is_date:
                region = strip_date_label(region)
                region = _preprocess_date_region(region)
            elif field.localize == "ink":
                localized = crop_to_ink(region, dpi=dpi)
                if localized is not None:
                    region = localized
            if field.postprocess not in _DATE_FIELDS:
                region = upscale_for_ocr(region, min_side=800)

        use_date_engine = (
            date_engine is not None
            and is_date
        )
        key = "date" if use_date_engine else "main"
        engines.setdefault(key, []).append((index, region))
        positions.setdefault(key, []).append(index)

    for key in ("main", "date"):
        items = engines.get(key)
        if not items:
            continue
        target = date_engine if key == "date" else engine
        if target is None:
            target = engine
        batch = target.recognize_batch([crop for _, crop in items])
        for (orig_idx, _crop), lines in zip(items, batch):
            field = fields[orig_idx]
            texts = [line.text.strip() for line in lines if line.text.strip()]
            text = " ".join(texts)
            confidence = round(
                sum(line.confidence for line in lines) / len(lines), 3
            ) if lines else 0.0
            logger.debug(f"OCR {field.id}: {text!r} (conf={confidence})")
            results[orig_idx] = (text, confidence)

    return results


def ocr_region(
    engine: OcrEngine,
    page: np.ndarray,
    field: FieldTemplate,
    crop_padding: float = 0.01,
    preprocess: bool = True,
    dpi: Optional[int] = None,
    date_engine: Optional[OcrEngine] = None,
) -> Tuple[str, float]:
    """Aplica OCR a la región de un solo campo de la plantilla."""
    text, confidence = ocr_regions(
        engine, page, [field], crop_padding,
        preprocess, dpi, date_engine=date_engine,
    )[0]
    return text, confidence

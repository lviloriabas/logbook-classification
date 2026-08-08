"""OCR aplicado únicamente sobre regiones definidas por la plantilla."""

from __future__ import annotations

from typing import List, Tuple

import numpy as np
from loguru import logger

from app.models.schemas import OcrResult
from app.ocr.engine import OcrEngine
from app.templates.schema import FieldTemplate
from app.vision.ink_extent import crop_to_ink
from app.vision.preprocessing import crop_region, upscale_for_ocr


def ocr_regions(
    engine: OcrEngine,
    page: np.ndarray,
    fields: List[FieldTemplate],
    crop_padding: float = 0.01,
    preprocess: bool = True,
) -> List[Tuple[str, float]]:
    """Aplica OCR a varias regiones en una sola llamada al motor.

    Args:
        engine: Motor OCR a utilizar.
        page: Página alineada (BGR).
        fields: Campos con coordenadas relativas.
        crop_padding: Margen relativo de los recortes.
        preprocess: Si True (por defecto) localiza la tinta y reescala el
            recorte antes del OCR; si False, se envía el recorte crudo
            (para comparar motores con escritura a mano).

    Returns:
        Lista (mismo orden que ``fields``) de (texto unido, confianza 0-1).
    """
    crops: List[np.ndarray] = []
    positions: List[int] = []
    results: List[Tuple[str, float]] = []

    for index, field in enumerate(fields):
        try:
            region = crop_region(page, field, pad=crop_padding)
        except ValueError as exc:
            logger.warning(f"OCR {field.id}: {exc}")
            results.append(("", 0.0))
            continue
        if preprocess:
            if field.localize == "ink":
                localized = crop_to_ink(region)
                if localized is not None:
                    region = localized
            region = upscale_for_ocr(region, min_side=800)
        crops.append(region)
        positions.append(index)

    batches = engine.recognize_batch(crops) if crops else []
    for index, lines in zip(positions, batches):
        field = fields[index]
        texts = [line.text.strip() for line in lines if line.text.strip()]
        text = " ".join(texts)

        if lines:
            confidence = round(
                sum(line.confidence for line in lines) / len(lines), 3
            )
        else:
            confidence = 0.0

        logger.debug(f"OCR {field.id}: {text!r} (conf={confidence})")
        results.append((text, confidence))
    return results


def ocr_region(
    engine: OcrEngine,
    page: np.ndarray,
    field: FieldTemplate,
    crop_padding: float = 0.01,
    preprocess: bool = True,
) -> Tuple[str, float]:
    """Aplica OCR a la región de un solo campo de la plantilla.

    Args:
        engine: Motor OCR a utilizar.
        page: Página alineada (BGR).
        field: Campo con coordenadas relativas.
        crop_padding: Margen relativo del recorte.
        preprocess: Ver ocr_regions.

    Returns:
        (texto unido, confianza promedio 0-1).
    """
    text, confidence = ocr_regions(engine, page, [field], crop_padding,
                                   preprocess)[0]
    return text, confidence

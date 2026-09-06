"""Lectura por ranuras de las casillas de fecha.

La casilla de fecha viene partida por separadores verticales impresos,
así que se puede leer carácter por carácter en vez de como una línea
suelta. ``read_date_slots`` lee cada ranura con el motor OCR de la
ejecución y ``decode_slots`` decide qué palabra forman: el motor no
admite una lista de caracteres permitidos, así que la restricción
(dígitos para día/año, letras de mes para el mes) se aplica al puntuar
las lecturas, no al pedirlas.

Nunca lanza excepciones ni introduce valores basura: si no lee nada,
el campo queda como estaba.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

import numpy as np
from loguru import logger

from app.ocr.engine import OcrEngine
from app.ocr.month_evidence import rank_month_slots
from app.vision.preprocessing import upscale_for_ocr

def decode_slots(
    rule: str, readings: List[Tuple[str, float]]
) -> Tuple[str, float]:
    """Reconstruye el valor de una casilla partida por separadores.

    Args:
        rule: 'day', 'month' o 'year' (decide la restricción).
        readings: (carácter leído o '', confianza 0-1) por ranura.

    Returns:
        (texto, confianza) o ("", 0.0) si la casilla no se puede
        reconstruir con restricciones.
    """
    confidence_scores = [c for _, c in readings if c > 0]
    confidence = (
        round(sum(confidence_scores) / len(confidence_scores), 3)
        if confidence_scores else 0.0
    )
    if not confidence_scores:
        return "", 0.0

    if rule in ("day", "year", "log_number", "digits"):
        aligned_digits = [ch if ch in "0123456789" else "" for ch, _ in readings]
        # En una casilla DD, un único dígito en la primera posición suele
        # significar que se perdió el segundo (p. ej. 25 -> 2), no el día 2.
        if rule == "day" and len(aligned_digits) == 2:
            if aligned_digits[0] and not aligned_digits[1]:
                return "", 0.0
        # El año ocupa todas sus posiciones. Un solo dígito es una lectura
        # incompleta de las dos casillas, no un año abreviado válido.
        if rule == "year" and not all(aligned_digits):
            return "", 0.0
        digits = "".join(aligned_digits)
        return (digits, confidence) if digits else ("", 0.0)

    if rule == "month":
        evidence = rank_month_slots(readings)
        return evidence.word, evidence.confidence

    return "", 0.0


def read_date_slots(
    field_id: str,
    postprocess: Optional[str],
    slots: List[np.ndarray],
    preprocess: bool = True,
    engine: Optional[OcrEngine] = None,
) -> Optional[Tuple[str, float]]:
    """Lee una casilla partida en ranuras, carácter por carácter.

    Args:
        field_id: Id del campo (day/month/year/log_number).
        postprocess: Postprocesador del campo (decide la regla).
        slots: Sub-imágenes de las ranuras (ver crop_slots).
        preprocess: Si True reescala cada ranura antes de leerla; si
            False, lee la ranura cruda.
        engine: Motor OCR de la ejecución. No acepta una lista de
            caracteres permitidos; las restricciones las aplican
            ``_read_slot_generic`` y ``decode_slots``.

    Returns:
        (texto, confianza) o None si no se puede leer nada.
    """
    if not slots or engine is None:
        return None
    rule = postprocess or field_id

    readings: List[Tuple[str, float]] = []
    for slot in slots:
        text, conf = _read_slot_generic(engine, slot, rule, preprocess)
        readings.append((text, conf))

    text, confidence = decode_slots(rule, readings)
    if not text:
        return None
    logger.debug(f"OCR por ranuras {field_id}: {text!r} (conf={confidence})")
    return text, confidence



def _read_slot_generic(
    engine: OcrEngine, slot: np.ndarray, rule: str,
    preprocess: bool = True,
) -> Tuple[str, float]:
    """Lee una ranura con un motor sin whitelist (p. ej. PaddleOCR).

    El filtrado por regla (dígito/letra) lo hace el llamador: aquí solo se
    conserva el primer carácter del tipo esperado, si existe.
    """
    try:
        region = slot
        if preprocess:
            region = upscale_for_ocr(slot, min_side=80)
        lines = engine.recognize(region)
    except Exception as exc:  # noqa: BLE001 - el respaldo nunca debe romper
        logger.debug(f"Lectura de ranura falló: {exc}")
        return "", 0.0
    best_line = max(lines, key=lambda line: line.confidence) if lines else None
    if best_line is None:
        return "", 0.0
    text = best_line.text.strip()
    if not text:
        return "", 0.0
    try:
        conf = max(0.0, min(1.0, float(best_line.confidence)))
    except (TypeError, ValueError):
        conf = 0.0
    if rule in ("day", "year", "log_number", "digits"):
        allowed = [ch for ch in text if ch in "0123456789"]
    elif rule == "month":
        allowed = [ch for ch in text.upper() if ch.isascii() and (ch.isalnum() or ch == "/")]
    else:
        allowed = list(text)
    return (allowed[0], conf) if allowed else ("", 0.0)

"""Redundancia OCR (Tesseract restringido) para campos críticos.

PaddleOCR (modelo general) sufre con las casillas diminutas de fecha,
matrícula y log_number: etiquetas impresas, separadores verticales de
casilla y escritura degradada. Cuando el postproceso de un campo crítico
queda vacío (o muy débil), se reintenta el recorte localizado por tinta
con Tesseract en modo restringido (PSM 7, whitelist según el campo:
dígitos para día/año/log_number, letras de meses para mes, alfanumérico
para matrícula).

Es una redundancia interna automática: el motor principal sigue siendo
PaddleOCR y el usuario no elige nada. El respaldo nunca lanza excepciones
ni introduce valores basura: si no lee nada, el campo queda como estaba.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

import numpy as np
from loguru import logger

from app.ocr.engine import OcrEngine, TesseractOcrEngine
from app.utils.io import resolve_tesseract_path
from app.utils.postprocess import MONTH_WORDS
from app.vision.preprocessing import upscale_for_ocr

# PSM 7 = una sola línea; whitelist restringe el alfabeto a dígitos.
_DIGITS_CONFIG = "--psm 7 -c tessedit_char_whitelist=0123456789"
# Meses: letras (español/inglés) + dígitos (año contaminado al límite).
_MONTH_CONFIG = (
    "--psm 7 -c tessedit_char_whitelist="
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
)
# Matrícula: alfanumérico + guion (HP-xxxxxxx), varias palabras.
_MATRICULA_CONFIG = (
    "--psm 6 -c tessedit_char_whitelist="
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-"
)
# PSM 10 = carácter individual; whitelist según la ranura (dígito/letra).
_SLOT_DIGIT_CONFIG = "--psm 10 -c tessedit_char_whitelist=0123456789"
_SLOT_LETTER_CONFIG = (
    "--psm 10 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ"
)
# Penalización de una ranura vacía (o no leída) al puntuar un mes.
EMPTY_SLOT_SCORE = 0.05
MISMATCH_SLOT_SCORE = 0.03

_FALLBACK: Optional[OcrEngine] = None
_FALLBACK_CHECKED = False


def _fallback_engine() -> Optional[OcrEngine]:
    """Motor de respaldo perezoso (una vez por proceso)."""
    global _FALLBACK, _FALLBACK_CHECKED
    if _FALLBACK_CHECKED:
        return _FALLBACK
    _FALLBACK_CHECKED = True
    if resolve_tesseract_path() is None:
        logger.debug("OCR de respaldo desactivado: sin tesseract")
        return None
    try:
        _FALLBACK = TesseractOcrEngine(lang="eng")
    except Exception as exc:  # noqa: BLE001 - el respaldo nunca debe romper
        logger.warning(f"OCR de respaldo desactivado: {exc}")
        _FALLBACK = None
    return _FALLBACK


def _config_for(field_id: str, postprocess: Optional[str]) -> str:
    """Config de Tesseract según el tipo de campo.

    Se prioriza el ``postprocess`` (day/month/year/matricula/…); el
    ``field_id`` se usa como respaldo para los campos sin postproceso.
    """
    rule = postprocess or field_id
    if rule in ("day", "year", "log_number", "digits"):
        return _DIGITS_CONFIG
    if rule == "month":
        return _MONTH_CONFIG
    if rule == "matricula":
        return _MATRICULA_CONFIG
    return _MONTH_CONFIG


def ocr_fallback(
    field_id: str,
    postprocess: Optional[str],
    region,
) -> Optional[Tuple[str, float]]:
    """Intenta leer un campo crítico con Tesseract restringido.

    Args:
        field_id: Id del campo (p. ej. 'day', 'matricula').
        postprocess: Postprocesador del campo ('day', 'month', 'year',
            'matricula', 'digits'...) que decide la whitelist.
        region: Recorte BGR/gris del campo (idealmente localizado por tinta).

    Returns:
        (texto, confianza) o None si no hay motor de respaldo o el OCR
        no devuelve texto.
    """
    engine = _fallback_engine()
    if engine is None:
        return None
    config = _config_for(field_id, postprocess)
    try:
        lines = engine.recognize(region, config=config)
    except Exception as exc:  # noqa: BLE001 - el respaldo nunca debe romper
        logger.debug(f"OCR de respaldo {field_id} falló: {exc}")
        return None
    texts = [line.text.strip() for line in lines if line.text.strip()]
    if not texts:
        return None
    text = " ".join(texts)
    confidence = (
        round(sum(line.confidence for line in lines) / len(lines), 3)
        if lines else 0.0
    )
    logger.debug(f"OCR de respaldo {field_id}: {text!r} (conf={confidence})")
    return text, confidence


def date_ocr_fallback(
    field_id: str, region
) -> Optional[Tuple[str, float]]:
    """Compat: respaldo para campos de fecha (día/mes/año).

    Args:
        field_id: 'day', 'month' o 'year'.
        region: Recorte BGR/gris del campo.

    Returns:
        (texto, confianza) o None si no se pudo leer.
    """
    return ocr_fallback(field_id, field_id, region)


# ── Decodificación por ranuras (casillas separadas por líneas) ──────────

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
        digits = "".join(ch for ch, _ in readings if ch in "0123456789")
        return (digits, confidence) if digits else ("", 0.0)

    if rule == "month":
        # Las ranuras se mantienen alineadas: una ranura vacía intermedia
        # no desplaza las letras posteriores (p. ej. 'J' '' 'L').
        aligned = [ch.upper() if ch.isalpha() else "" for ch, _ in readings]
        best_word, best_score = "", -1.0
        for word, numero in MONTH_WORDS:
            score = _month_slot_score(word, aligned, readings)
            if score > best_score:
                best_word, best_score = word, score
        if best_word:
            logger.debug(f"Mes por ranuras: {best_word!r} (score={best_score:.4f})")
            return best_word, confidence
    return "", 0.0


def _month_slot_score(
    word: str, chars: List[str], readings: List[Tuple[str, float]]
) -> float:
    """Puntuación de una abreviatura de mes contra las ranuras leídas."""
    score = 1.0
    for i, expected in enumerate(word):
        if i >= len(chars):
            score *= EMPTY_SLOT_SCORE
        elif chars[i] == expected:
            score *= readings[i][1] or EMPTY_SLOT_SCORE
        elif not chars[i]:
            score *= EMPTY_SLOT_SCORE
        else:
            score *= MISMATCH_SLOT_SCORE
    return score


def read_date_slots(
    field_id: str,
    postprocess: Optional[str],
    slots: List[np.ndarray],
    preprocess: bool = True,
) -> Optional[Tuple[str, float]]:
    """Lee una casilla partida en ranuras con Tesseract por carácter.

    Args:
        field_id: Id del campo (day/month/year/log_number).
        postprocess: Postprocesador del campo (decide la whitelist).
        slots: Sub-imágenes de las ranuras (ver crop_slots).
        preprocess: Si True reescala cada ranura antes de leerla; si
            False, lee la ranura cruda.

    Returns:
        (texto, confianza) o None si no se puede leer nada.
    """
    engine = _fallback_engine()
    if engine is None or not slots:
        return None
    rule = postprocess or field_id
    if rule in ("day", "year", "log_number", "digits"):
        config = _SLOT_DIGIT_CONFIG
    elif rule == "month":
        config = _SLOT_LETTER_CONFIG
    else:
        config = _MONTH_CONFIG

    readings: List[Tuple[str, float]] = []
    for slot in slots:
        text, conf = _read_slot(engine, slot, config, preprocess)
        readings.append((text, conf))

    text, confidence = decode_slots(rule, readings)
    if not text:
        return None
    logger.debug(f"OCR por ranuras {field_id}: {text!r} (conf={confidence})")
    return text, confidence


def _read_slot(
    engine: OcrEngine, slot: np.ndarray, config: str,
    preprocess: bool = True,
) -> Tuple[str, float]:
    """Lee un carácter de una ranura (PSM 10 + whitelist)."""
    try:
        region = slot
        if preprocess:
            region = upscale_for_ocr(slot, min_side=80)
        lines = engine.recognize(region, config=config)
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
    return text[0], conf
"""Ranuras (slots) de las casillas de fecha para OCR por carácter.

Las casillas day/month/year están divididas por líneas verticales
impresas entre cada carácter (p. ej. "2|0", "J|U|L"). Una sola línea
de OCR las lee como tokens partidos o los fusiona con etiquetas
vecinas. Esta módulo separa la casilla en celdas fijas usando la
posición de los separadores impresos (constantes de un formulario a
otro), para que el OCR de respaldo lea carácter por carácter.

    El mapa de ranuras se calcula sobre el mapa del fondo impreso, no sobre
    la página que contiene la escritura: así las posiciones de las ranuras
    permanecen estables aunque la tinta manuscrita varíe entre páginas.
"""

from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np

from app.templates.schema import Template
from app.vision.ink_extent import crop_to_ink, strip_date_label
from app.vision.preprocessing import crop_region

# Fracción mínima de filas oscuras de la banda para que una columna sea
# un separador (los rótulos impresos no llegan a ese porcentaje del alto).
SEP_MIN_RATIO = 0.45
# Ancho máximo (px) de una línea separadora (los rótulos son más anchos).
SEP_MAX_WIDTH = 10
# Ranuras mínimas/máximas aceptadas por casilla.
MIN_SLOTS = 2
MAX_SLOTS = 4
MIN_SLOT_WIDTH = 2

SLOT_EXPECTED = {"day": 2, "month": 3, "year": 2}


def _uniform_boundaries(left: int, right: int, expected: int) -> List[int]:
    """Bordes de ranuras de ancho uniforme si no se detectan separadores."""
    return [left] + [
        round(left + (right - left) * i / expected) for i in range(1, expected)
    ] + [right]


def compute_slot_map(printed_mask: np.ndarray, field) -> Optional[Dict]:
    """Detecta las ranuras de una casilla a partir del fondo impreso.

    Args:
        printed_mask: Máscara booleana del fondo impreso (mismo tamaño
            que la página alineada).
        field: FieldTemplate de la casilla (day/month/year/log_number).

    Returns:
        Dict con {'boundaries': [x0, x1, ...], 'slots': n} en coordenadas
        de página, o None si no se puede detectar (usar división uniforme).
    """
    height, width = printed_mask.shape[:2]
    left, top, right, bottom = field.rect_pixels(width, height)
    if right - left < 8 or bottom - top < 4:
        return None
    band = printed_mask[top:bottom, left:right]
    if band.size == 0:
        return None
    per_col = band.astype(np.float32).mean(axis=0)

    separators: List[int] = []
    i, size = 0, len(per_col)
    while i < size:
        if per_col[i] < SEP_MIN_RATIO:
            i += 1
            continue
        j = i
        while j < size and per_col[j] >= SEP_MIN_RATIO:
            j += 1
        if j - i <= SEP_MAX_WIDTH:
            separators.append((i + j) // 2 + left)
        i = j

    slots = len(separators) + 1
    if separators and MIN_SLOTS <= slots <= MAX_SLOTS:
        boundaries = [left] + separators + [right]
        return {"boundaries": boundaries, "slots": slots}
    return None


def build_slot_maps(printed_mask: np.ndarray, template: Template) -> Dict:
    """Mapa de ranuras por campo de fecha (fallback uniforme por campo).

    Args:
        printed_mask: Máscara del fondo impreso (página alineada).
        template: Plantilla con los campos day/month/year.

    Returns:
        {field_id: {"boundaries": [...], "slots": n}}.
    """
    maps: Dict[str, Dict] = {}
    for field_id, expected in SLOT_EXPECTED.items():
        field = template.field(field_id)
        if field is None:
            continue
        spec = compute_slot_map(printed_mask, field)
        if spec is None:
            left, top, right, bottom = field.rect_pixels(
                printed_mask.shape[1], printed_mask.shape[0]
            )
            boundaries = _uniform_boundaries(left, right, expected)
            spec = {"boundaries": boundaries, "slots": expected}
        maps[field_id] = spec
    return maps


def crop_slots(image, field, pad: float = 0.01, spec: Dict = None) -> Optional[List[np.ndarray]]:
    """Recorta la casilla y la divide en las ranuras del mapa.

    Args:
        image: Página alineada (BGR).
        field: FieldTemplate de la casilla.
        pad: Margen relativo del recorte (crop_padding, 0.01 por defecto).
        spec: Mapa de ranuras (de build_slot_maps/compute_slot_map).

    Returns:
        Lista de sub-imágenes (una por ranura) en el espacio de la página.
    """
    try:
        region = crop_region(image, field, pad)
    except ValueError:
        return None
    # crop_region devuelve (filas, columnas): la altura y el ancho reales.
    height, width = region.shape[:2]
    if width < MIN_SLOT_WIDTH * 2 or height < 4:
        return None

    left, top, right, bottom = field.rect_pixels(image.shape[1], image.shape[0])
    px = max(1, round(pad * (right - left)))
    region_left = max(0, left - px)

    landmarks = sorted(int(round(x)) for x in spec["boundaries"])
    slots: List[np.ndarray] = []
    for a, b in zip(landmarks, landmarks[1:]):
        x1 = a - region_left
        x2 = b - region_left
        if x2 - x1 < MIN_SLOT_WIDTH:
            continue
        x1 = max(0, x1)
        x2 = min(width, x2)
        if x2 <= x1:
            continue
        slots.append(region[:, x1:x2])
    return slots or None


def localize_slot(slot: np.ndarray, dpi: Optional[int] = None) -> np.ndarray:
    """Recorta una ranura al extento de su tinta (o la deja tal cual)."""
    slot = strip_date_label(slot)
    localized = crop_to_ink(slot, dpi=dpi)
    return localized if localized is not None else slot

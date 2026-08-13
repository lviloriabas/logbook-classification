"""Localización dinámica por página de la banda de fecha (grid snap).

La plantilla define las casillas day/month/year en coordenadas de la página
de referencia. La alineación global (similitud) deja residuos locales de
unos pocos píxeles —escaneos con deformación no uniforme, encuadernación,
márgenes distintos— que bastan para partir un dígito contra un separador
impreso o para que el OCR lea el separador como un '1'.

Este módulo ajusta la geometría por página: detecta las líneas impresas de
la retícula de fecha (bordes de casilla y separadores de carácter) en una
ventana alrededor de la posición de plantilla y encaja el peine esperado
(6 bordes + 4 separadores) con una traslación y una escala pequeñas. Es
dinámico: cada página se ajusta a su propia retícula impresa, que no cambia
de forma aunque la escritura manuscrita varíe.

Si la evidencia no es consistente, devuelve ``{}`` y el pipeline conserva
la geometría de plantilla (fallback seguro, nunca empeora el caso base).
"""

from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field
from typing import Dict, List, Optional, Tuple

import numpy as np
from loguru import logger

from app.templates.schema import FieldTemplate, Template
from app.vision.preprocessing import to_gray

DATE_FIELD_IDS: Tuple[str, ...] = ("day", "month", "year")
SLOT_COUNTS: Dict[str, int] = {"day": 2, "month": 3, "year": 2}

# Margen de búsqueda alrededor de la unión de casillas (fracción de página).
SEARCH_MARGIN_X = 0.01
SEARCH_MARGIN_Y = 0.03
# Cobertura mínima de una línea para considerarla parte de la retícula.
HLINE_MIN_RATIO = 0.5
VLINE_MIN_RATIO = 0.35
# Tolerancia de encaje de una posición del peine (fracción del ancho).
MATCH_TOLERANCE_RATIO = 0.004
SCALE_CANDIDATES: Tuple[float, ...] = (1.0, 0.99, 1.01, 0.98, 1.02)
# Fracción mínima del peine que debe encajar para aceptar el ajuste.
MIN_MATCH_SCORE = 0.8
# La banda ajustada no puede diferir de la plantilla más que esto en alto.
BAND_HEIGHT_RANGE = (0.6, 1.6)


@dataclass
class DateFieldGeometry:
    """Geometría ajustada de una casilla de fecha en píxeles de página."""

    field_id: str
    rect: Tuple[int, int, int, int]  # left, top, right, bottom
    separators: List[int] = dataclass_field(default_factory=list)
    score: float = 0.0

    def slot_spec(self) -> Dict:
        """Mapa de ranuras compatible con ``crop_slots``/``read_date_slots``."""
        left, _top, right, _bottom = self.rect
        boundaries = [left, *sorted(self.separators), right]
        return {"boundaries": boundaries, "slots": len(boundaries) - 1}


def _line_centers(
    ratios: np.ndarray, min_ratio: float, max_width: int
) -> List[int]:
    """Centros de las líneas: columnas/filas con cobertura >= min_ratio.

    Las líneas más gruesas que ``max_width`` se descartan (son texto o
    sombras, no la retícula).
    """
    centers: List[int] = []
    i, n = 0, len(ratios)
    while i < n:
        if ratios[i] < min_ratio:
            i += 1
            continue
        j = i
        while j < n and ratios[j] >= min_ratio:
            j += 1
        if j - i <= max_width:
            centers.append((i + j) // 2)
        i = j
    return centers


def _expected_comb(
    rects: Dict[str, Tuple[int, int, int, int]],
    union_left: int,
    union_width: int,
) -> List[Tuple[float, str, str]]:
    """Peine esperado: bordes de casilla y separadores, normalizados 0-1.

    Returns:
        Lista de (posición relativa a la unión, field_id, etiqueta), donde
        la etiqueta es 'left', 'right' o 'sep<k>'.
    """
    comb: List[Tuple[float, str, str]] = []
    for field_id in DATE_FIELD_IDS:
        left, _top, right, _bottom = rects[field_id]
        comb.append(((left - union_left) / union_width, field_id, "left"))
        comb.append(((right - union_left) / union_width, field_id, "right"))
        slots = SLOT_COUNTS[field_id]
        for k in range(1, slots):
            absolute = left + (right - left) * k / slots
            comb.append((
                (absolute - union_left) / union_width, field_id, f"sep{k}"
            ))
    return comb


def _match_comb(
    comb: List[Tuple[float, str, str]],
    vlines: List[int],
    union_left: int,
    union_width: int,
    tolerance: float,
    max_shift: int,
) -> Optional[Tuple[float, float, float, Dict[Tuple[str, str], int]]]:
    """Encaja el peine contra las líneas detectadas.

    Returns:
        (score, dx, escala, {clave: posición}) del mejor encaje, o None.
        Los bordes exteriores (day.left, year.right) deben encajar siempre.
    """
    best: Optional[Tuple[tuple, float, float, Dict[Tuple[str, str], int]]] = None
    for anchor in vlines:
        dx = anchor - union_left
        if abs(dx) > max_shift:
            continue
        for scale in SCALE_CANDIDATES:
            matched: Dict[Tuple[str, str], int] = {}
            for rel, field_id, kind in comb:
                predicted = union_left + dx + scale * rel * union_width
                nearest = min(vlines, key=lambda v: abs(v - predicted))
                if abs(nearest - predicted) <= tolerance:
                    matched[(field_id, kind)] = nearest
            if ("day", "left") not in matched or ("year", "right") not in matched:
                continue
            score = len(matched) / len(comb)
            key = (score, -abs(dx), -abs(1.0 - scale))
            if best is None or key > best[0]:
                best = (key, float(dx), scale, matched)
    if best is None or best[0][0] < MIN_MATCH_SCORE:
        return None
    _key, dx, scale, matched = best
    return best[0][0], dx, scale, matched


def locate_date_grid(
    image: np.ndarray,
    template: Template,
    ink_threshold: int = 185,
    field_ids: Tuple[str, ...] = DATE_FIELD_IDS,
) -> Dict[str, DateFieldGeometry]:
    """Ajusta las casillas de fecha a la retícula impresa de ESTA página.

    Args:
        image: Página alineada (BGR o gris) a cualquier DPI.
        template: Plantilla con los campos day/month/year.
        ink_threshold: Umbral de gris para considerar un píxel impreso.
        field_ids: Campos de fecha a localizar.

    Returns:
        {field_id: DateFieldGeometry} con rectángulos y separadores en
        píxeles de ``image``; ``{}`` si la retícula no encaja (fallback a
        la geometría de plantilla).
    """
    height, width = image.shape[:2]
    fields = {fid: template.field(fid) for fid in field_ids}
    if any(f is None for f in fields.values()):
        return {}
    rects = {
        fid: f.rect_pixels(width, height)  # type: ignore[union-attr]
        for fid, f in fields.items()
    }
    ux0 = min(r[0] for r in rects.values())
    uy0 = min(r[1] for r in rects.values())
    ux1 = max(r[2] for r in rects.values())
    uy1 = max(r[3] for r in rects.values())
    union_width = ux1 - ux0
    union_height = uy1 - uy0
    if union_width < 20 or union_height < 6:
        return {}

    mx = round(width * SEARCH_MARGIN_X)
    my = round(height * SEARCH_MARGIN_Y)
    wx0, wx1 = max(0, ux0 - mx), min(width, ux1 + mx)
    wy0, wy1 = max(0, uy0 - my), min(height, uy1 + my)

    gray = to_gray(image)
    window = gray[wy0:wy1, wx0:wx1]
    if window.size == 0:
        return {}
    dark = window < ink_threshold

    # Bordes horizontales de la banda (si no encajan, se conserva la y de
    # plantilla: el ajuste vertical es opcional, el horizontal es el que
    # separa dígitos de separadores).
    max_hline = max(4, round(height * 0.004))
    row_ratio = dark.mean(axis=1)
    hcenters = [c + wy0 for c in _line_centers(row_ratio, HLINE_MIN_RATIO, max_hline)]
    top = bottom = None
    tops = [c for c in hcenters if abs(c - uy0) <= my]
    bottoms = [c for c in hcenters if abs(c - uy1) <= my]
    if tops and bottoms:
        cand_top = min(tops, key=lambda c: abs(c - uy0))
        cand_bottom = min(bottoms, key=lambda c: abs(c - uy1))
        if (cand_bottom > cand_top and BAND_HEIGHT_RANGE[0] * union_height
                <= cand_bottom - cand_top <= BAND_HEIGHT_RANGE[1] * union_height):
            top, bottom = cand_top, cand_bottom
    if top is None:
        top, bottom = uy0, uy1

    band = dark[top - wy0:bottom - wy0, :]
    if band.size == 0 or band.shape[0] < 4:
        return {}
    max_vline = max(4, round(width * 0.004))
    col_ratio = band.mean(axis=0)
    vlines = [c + wx0 for c in _line_centers(col_ratio, VLINE_MIN_RATIO, max_vline)]
    if len(vlines) < 6:
        return {}

    comb = _expected_comb(rects, ux0, union_width)
    tolerance = max(3.0, width * MATCH_TOLERANCE_RATIO)
    match = _match_comb(comb, vlines, ux0, union_width, tolerance, mx)
    if match is None:
        return {}
    score, dx, scale, matched = match

    result: Dict[str, DateFieldGeometry] = {}
    for fid in field_ids:
        rect = rects[fid]
        left = matched.get(
            (fid, "left"), ux0 + dx + scale * (rect[0] - ux0)
        )
        right = matched.get(
            (fid, "right"), ux0 + dx + scale * (rect[2] - ux0)
        )
        separators: List[int] = []
        slots = SLOT_COUNTS[fid]
        for k in range(1, slots):
            position = matched.get((fid, f"sep{k}"))
            if position is not None:
                separators.append(position)
        if len(separators) != slots - 1:
            # Separador no detectado: división uniforme del borde ajustado.
            separators = [
                round(left + (right - left) * k / slots)
                for k in range(1, slots)
            ]
        snapped = (
            int(round(left)), top, int(round(right)), bottom
        )
        result[fid] = DateFieldGeometry(fid, snapped, separators, score)

    logger.debug(
        f"Retícula de fecha ajustada: dx={dx:+.1f} escala={scale:.3f} "
        f"encaje={score:.2f}"
    )
    return result


def field_override(
    geometry: DateFieldGeometry,
    base: FieldTemplate,
    image_shape: Tuple[int, ...],
) -> FieldTemplate:
    """Convierte la geometría ajustada en un FieldTemplate relativo.

    El ``id`` y el resto de reglas se conservan; solo cambian x/y/w/h.
    """
    height, width = image_shape[:2]
    left, top, right, bottom = geometry.rect
    return base.model_copy(update={
        "x": left / width,
        "y": top / height,
        "w": (right - left) / width,
        "h": (bottom - top) / height,
    })

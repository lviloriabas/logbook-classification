"""Localización de tinta manuscrita dentro de una región.

Las regiones de campos pequeños (día/mes/año) contienen etiquetas
impresas, bordes y sombras que contaminan el OCR. ``crop_to_ink``
sub-recorta la región al extento de la tinta de tamaño "escritura":

1. Binarización adaptativa (robusta a sombras).
2. Eliminación de componentes que son líneas impresas, el marco de la
   celda impresa o cubren casi toda la región (blobs/sellos). La
   escritura manuscrita que toca el borde del recorte se conserva: los
   recortes de campos pequeños son ajustados y los dígitos caen sobre
   el borde.
3. Todos los umbrales en píxeles se escalan por ``scale`` (= dpi/150)
   para que el comportamiento sea el mismo a cualquier resolución de
   render (los recortes a 400 DPI son ~2.7× los de 150 DPI).
4. Si la tinta restante está concentrada en una zona pequeña, devuelve
   el sub-recorte con un margen; si no, devuelve None (usar el recorte
   completo).
"""

from __future__ import annotations

from typing import Optional, Tuple

import cv2
import numpy as np

# Componente con estas dimensiones se considera línea impresa (regla, marco).
# Valores nominales a 150 DPI; se multiplican por ``scale``.
MAX_LINE_THICKNESS = 4
MIN_LINE_LENGTH = 10
# Altura por debajo de la cual un componente se considera texto impreso
# pequeño (etiquetas): a 150 DPI miden 7-8 px y la escritura ~20-30 px.
MIN_WRITING_HEIGHT = 9
# Fracción del área del recorte sobre la cual un componente es un blob
# (sombra, sello, celda rellena) y se descarta.
MAX_BLOB_AREA_RATIO = 0.15
# El sub-recorte se acepta solo si la tinta ocupa una fracción reducida
# del recorte (si está dispersa, es texto impreso, no escritura).
MAX_SUBCROP_AREA_RATIO = 0.75
MAX_SUBCROP_WIDTH_RATIO = 0.85
# Margen (px) alrededor de la tinta en el sub-recorte (a 150 DPI).
INK_MARGIN = 4
# Área mínima (px²) de un componente para no ser ruido (a 150 DPI).
MIN_AREA = 4
# DPI de referencia al que están calibrados los umbrales nominales.
REFERENCE_DPI = 150


def strip_date_label(region: np.ndarray) -> np.ndarray:
    """Blanquea la franja superior donde viven los rótulos de fecha.

    Los formularios imprimen ``Day``, ``Month`` y ``Year`` dentro de la
    misma caja que la escritura. La máscara por consenso no puede separar
    ese texto de una escritura que se repite en varias páginas.
    """
    height = region.shape[0]
    cut = max(1, round(height * 0.22))
    result = region.copy()
    result[:cut] = 255
    return result


def _dpi_scale(dpi: Optional[int]) -> float:
    """Factor de escala de umbrales para el DPI de render.

    Args:
        dpi: DPI del render. None o <=0 asumen el DPI de referencia.

    Returns:
        Factor (1.0 a 150 DPI, ~2.67 a 400 DPI).
    """
    if dpi is None or dpi <= 0:
        return 1.0
    return dpi / REFERENCE_DPI


def crop_to_ink(
    region: np.ndarray,
    margin: Optional[int] = None,
    dpi: Optional[int] = None,
) -> Optional[np.ndarray]:
    """Sub-recorta ``region`` al extento de la tinta manuscrita.

    Args:
        region: Recorte (BGR o gris) del campo.
        margin: Píxeles de margen extra alrededor de la tinta. Si es
            None, se usa ``INK_MARGIN`` escalado por el DPI.
        dpi: DPI del render para escalar los umbrales en píxeles
            (None = 150 DPI).

    Returns:
        Sub-imagen con la tinta localizada, o None si no se puede
        aislar con confianza (usar la región completa).
    """
    height, width = region.shape[:2]
    if height < 10 or width < 10:
        return None

    scale = _dpi_scale(dpi)
    if margin is None:
        margin = int(round(INK_MARGIN * scale))

    gray = region if region.ndim == 2 else cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
    binary = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV, 31, 12,
    )
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))

    max_line_thickness = MAX_LINE_THICKNESS * scale
    min_line_length = MIN_LINE_LENGTH * scale
    min_writing_height = MIN_WRITING_HEIGHT * scale
    min_area = MIN_AREA * scale * scale

    n, labels, stats, _ = cv2.connectedComponentsWithStats(binary, 8)
    kept: list[Tuple[int, int, int, int]] = []  # (x, y, w, h)
    for i in range(1, n):
        x = int(stats[i, cv2.CC_STAT_LEFT])
        y = int(stats[i, cv2.CC_STAT_TOP])
        w = int(stats[i, cv2.CC_STAT_WIDTH])
        h = int(stats[i, cv2.CC_STAT_HEIGHT])
        area = int(stats[i, cv2.CC_STAT_AREA])

        # Marco de la celda impresa: su bbox abarca casi todo el recorte.
        if w >= width - 2 and h >= height - 2:
            continue
        # Líneas impresas (reglas de celdas, subrayados).
        if (w >= min_line_length and h <= max_line_thickness) or (
            h >= min_line_length and w <= max_line_thickness
        ):
            continue
        # Texto impreso pequeño (etiquetas): su altura no llega a la de la
        # escritura manuscrita. Escalado por DPI.
        if h <= min_writing_height:
            continue
        # Blobs gigantes (sombra, sello, celda rellena).
        if area > width * height * MAX_BLOB_AREA_RATIO:
            continue
        # Ruido de unos pocos píxeles.
        if area < min_area:
            continue
        kept.append((x, y, w, h))

    if not kept:
        return None

    min_x = min(c[0] for c in kept)
    min_y = min(c[1] for c in kept)
    max_x = max(c[0] + c[2] for c in kept)
    max_y = max(c[1] + c[3] for c in kept)

    sub_w = max_x - min_x
    sub_h = max_y - min_y
    if (
        sub_w > width * MAX_SUBCROP_WIDTH_RATIO
        or sub_w * sub_h > width * height * MAX_SUBCROP_AREA_RATIO
    ):
        return None

    left = max(0, min_x - margin)
    top = max(0, min_y - margin)
    right = min(width, max_x + margin)
    bottom = min(height, max_y + margin)
    if right - left < 4 or bottom - top < 4:
        return None
    return region[top:bottom, left:right]

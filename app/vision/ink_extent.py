"""Localización de tinta manuscrita dentro de una región.

Las regiones de campos pequeños (día/mes/año) contienen etiquetas
impresas, bordes y sombras que contaminan el OCR. ``crop_to_ink``
sub-recorta la región al extento de la tinta de tamaño "escritura":

1. Binarización adaptativa (robusta a sombras).
2. Eliminación de componentes que son líneas impresas, tocan el borde
   del recorte o cubren casi toda la región (blobs/sellos).
3. Si la tinta restante está concentrada en una zona pequeña, devuelve
   el sub-recorte con un margen; si no, devuelve None (usar el recorte
   completo).
"""

from __future__ import annotations

from typing import Optional, Tuple

import cv2
import numpy as np

# Componente con estas dimensiones se considera línea impresa (regla, marco).
MAX_LINE_THICKNESS = 4
MIN_LINE_LENGTH = 10
# Fracción del área del recorte sobre la cual un componente es un blob
# (sombra, sello, celda rellena) y se descarta.
MAX_BLOB_AREA_RATIO = 0.15
# El sub-recorte se acepta solo si la tinta ocupa una fracción reducida
# del recorte (si está dispersa, es texto impreso, no escritura).
MAX_SUBCROP_AREA_RATIO = 0.75
MAX_SUBCROP_WIDTH_RATIO = 0.85
# Margen (px) alrededor de la tinta en el sub-recorte.
INK_MARGIN = 4


def crop_to_ink(
    region: np.ndarray, margin: int = INK_MARGIN
) -> Optional[np.ndarray]:
    """Sub-recorta ``region`` al extento de la tinta manuscrita.

    Args:
        region: Recorte (BGR o gris) del campo.
        margin: Píxeles de margen extra alrededor de la tinta.

    Returns:
        Sub-imagen con la tinta localizada, o None si no se puede
        aislar con confianza (usar la región completa).
    """
    height, width = region.shape[:2]
    if height < 10 or width < 10:
        return None

    gray = region if region.ndim == 2 else cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
    binary = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV, 31, 12,
    )
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))

    n, labels, stats, _ = cv2.connectedComponentsWithStats(binary, 8)
    kept: list[Tuple[int, int, int, int]] = []  # (x, y, w, h)
    for i in range(1, n):
        x = int(stats[i, cv2.CC_STAT_LEFT])
        y = int(stats[i, cv2.CC_STAT_TOP])
        w = int(stats[i, cv2.CC_STAT_WIDTH])
        h = int(stats[i, cv2.CC_STAT_HEIGHT])
        area = int(stats[i, cv2.CC_STAT_AREA])

        # Líneas impresas (reglas de celdas, subrayados).
        if (w >= MIN_LINE_LENGTH and h <= MAX_LINE_THICKNESS) or (
            h >= MIN_LINE_LENGTH and w <= MAX_LINE_THICKNESS
        ):
            continue
        # Texto impreso pequeño (etiquetas, glifos de 6-9 px): la escritura
        # manuscrita de estos campos mide ~20-30 px de alto.
        if h <= 9:
            continue
        # Bordes del recorte (marco de celda impreso).
        if x == 0 or y == 0 or x + w >= width - 1 or y + h >= height - 1:
            continue
        # Blobs gigantes (sombra, sello, celda rellena).
        if area > width * height * MAX_BLOB_AREA_RATIO:
            continue
        # Ruido de un píxel.
        if area < 4:
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

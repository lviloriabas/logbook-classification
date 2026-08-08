"""Detección de páginas en blanco."""

from __future__ import annotations

import cv2
import numpy as np

from app.vision.preprocessing import to_gray


def is_blank(image: np.ndarray, threshold: float = 15.0) -> bool:
    """Determina si una página está en blanco por varianza de grises.

    Las páginas escaneadas en blanco tienen varianza muy baja (≈ ruido
    del escáner). Un umbral de 15 es seguro para escaneos a 200 DPI.

    Args:
        image: Página en BGR.
        threshold: Varianza máxima para considerarla en blanco.

    Returns:
        True si la página se considera en blanco.
    """
    gray = to_gray(image)
    variance = float(gray.var())
    return variance < threshold

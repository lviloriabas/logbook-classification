"""Análisis de tinta compartido por firmas y checkboxes."""

from __future__ import annotations

import cv2
import numpy as np

from app.models.schemas import InkAnalysis
from app.vision.preprocessing import to_gray


def analyze_ink(region: np.ndarray) -> InkAnalysis:
    """Analiza la cobertura de tinta de una región.

    Binariza con Otsu (invertida: tinta = blanco) y mide:
    - ink_ratio: fracción de píxeles con tinta.
    - component_count: número de trazos (componentes conexas).
    - max_component_area: área del trazo más grande.

    Args:
        region: Sub-imagen BGR de la región a analizar.

    Returns:
        InkAnalysis con las métricas.
    """
    gray = to_gray(region)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    _, binary = cv2.threshold(gray, 0, 255,
                              cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    ink_ratio = float(np.count_nonzero(binary)) / max(binary.size, 1)

    num_labels, _, stats, _ = cv2.connectedComponentsWithStats(
        binary, connectivity=8
    )
    components = [
        stats[i] for i in range(1, num_labels)
        if stats[i][cv2.CC_STAT_AREA] >= 3
    ]
    max_area = max((c[cv2.CC_STAT_AREA] for c in components),
                   default=0.0)
    return InkAnalysis(
        ink_ratio=round(ink_ratio, 4),
        component_count=len(components),
        max_component_area=float(max_area),
    )

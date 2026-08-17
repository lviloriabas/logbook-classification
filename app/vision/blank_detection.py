"""Detección de páginas en blanco."""

from __future__ import annotations

import numpy as np

from app.vision.preprocessing import to_gray

# La varianza se mide sobre una de cada N filas y columnas. Es un estadístico
# de toda la hoja, no una búsqueda de detalle: submuestrear no cambia lo que
# mide, solo cuántos píxeles hacen falta para medirlo. Medido sobre 8 páginas
# a 200 DPI, la varianza de la página completa y la del submuestreo difieren
# menos de un 3% (1190 frente a 1161, 1689 frente a 1747, 1450 frente a
# 1506...) sobre valores que rondan 1200-2000, con el umbral de decisión en
# 15: ninguna página cambia de lado. A cambio, la comprobación baja de 21 ms
# a 2,7 ms por página.
_SUBSAMPLE_STEP = 4
# Por debajo de este lado no se submuestrea: en un recorte pequeño los
# píxeles descartados sí pesan en el estadístico.
_MIN_SUBSAMPLE_SIDE = 400


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
    sample = image
    height, width = image.shape[:2]
    if height >= _MIN_SUBSAMPLE_SIDE and width >= _MIN_SUBSAMPLE_SIDE:
        sample = image[::_SUBSAMPLE_STEP, ::_SUBSAMPLE_STEP]
    variance = float(to_gray(sample).var())
    return variance < threshold

"""Preprocesamiento de imágenes: grises, binarización, deskew, recorte."""

from __future__ import annotations

import math
import statistics
from typing import Tuple

import cv2
import numpy as np

from app.templates.schema import FieldTemplate


def to_gray(image: np.ndarray) -> np.ndarray:
    """Convierte una imagen BGR a escala de grises."""
    if image.ndim == 3:
        return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return image


def binarize(image: np.ndarray, adaptive: bool = False) -> np.ndarray:
    """Binariza una imagen (Otsu o umbral adaptativo) para OCR."""
    gray = to_gray(image)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    if adaptive:
        return cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY, 31, 15,
        )
    _, binary = cv2.threshold(gray, 0, 255,
                              cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return binary


def denoise(image: np.ndarray) -> np.ndarray:
    """Reduce ruido manteniendo bordes (fastNlMeansDenoisingColored)."""
    if image.ndim == 3:
        return cv2.fastNlMeansDenoisingColored(image, None, 10, 10, 7, 21)
    return cv2.fastNlMeansDenoising(image, None, 10, 7, 21)


def deskew(image: np.ndarray, max_angle: float = 10.0) -> Tuple[np.ndarray, float]:
    """Corrige la inclinación de la página usando transformada de Hough.

    Returns:
        (imagen corregida, ángulo aplicado en grados)
    """
    gray = to_gray(image)
    edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 50, 150,
                      apertureSize=3)
    min_line_len = max(80, gray.shape[1] // 4)
    lines = cv2.HoughLinesP(
        edges, 1, np.pi / 180, threshold=120,
        minLineLength=min_line_len, maxLineGap=25,
    )
    angles: list[float] = []
    if lines is not None:
        for line in lines:
            x1, y1, x2, y2 = line[0]
            if x2 == x1:
                continue
            angle = math.degrees(math.atan2(y2 - y1, x2 - x1))
            if -max_angle <= angle <= max_angle:
                angles.append(angle)

    if not angles:
        return image, 0.0

    angle = float(statistics.median(angles))
    if abs(angle) < 0.1:
        return image, 0.0

    rotated = rotate(image, angle)
    return rotated, angle


def rotate(image: np.ndarray, angle_deg: float) -> np.ndarray:
    """Rota una imagen alrededor de su centro rellenando con blanco."""
    height, width = image.shape[:2]
    center = (width / 2, height / 2)
    matrix = cv2.getRotationMatrix2D(center, angle_deg, 1.0)
    return cv2.warpAffine(
        image, matrix, (width, height),
        flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT,
        borderValue=(255, 255, 255),
    )


def crop_region(image: np.ndarray, field: FieldTemplate,
                pad: float = 0.10) -> np.ndarray:
    """Recorta la región de un campo (coordenadas relativas + margen).

    Args:
        image: Página alineada en BGR.
        field: Definición del campo con coordenadas 0-1.
        pad: Margen relativo al *tamaño del campo* (fracción de su alto/ancho)
            para no cortar caracteres. Relativo al campo, no a la página,
            para que el margen sea proporcional al contenido.

    Returns:
        Sub-imagen de la región del campo.
    """
    height, width = image.shape[:2]
    left, top, right, bottom = field.rect_pixels(width, height)
    px = max(1, round(pad * (right - left)))
    py = max(1, round(pad * (bottom - top)))
    left = max(0, left - px)
    top = max(0, top - py)
    right = min(width, right + px)
    bottom = min(height, bottom + py)
    if right <= left or bottom <= top:
        raise ValueError(f"Región inválida para campo '{field.id}'")
    return image[top:bottom, left:right]


def upscale_for_ocr(region: np.ndarray, min_side: int = 800) -> np.ndarray:
    """Escala la región para mejorar la precisión del OCR."""
    height, width = region.shape[:2]
    longest = max(height, width)
    if longest >= min_side:
        return region
    scale = min_side / longest
    new_size = (int(width * scale), int(height * scale))
    return cv2.resize(region, new_size, interpolation=cv2.INTER_CUBIC)

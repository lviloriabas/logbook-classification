"""Alineación automática de páginas contra una imagen de referencia.

Soporta rotación, traslación y pequeñas diferencias de escala mediante
coincidencia de características ORB + ajuste de similitud (RANSAC).

Se usa una transformación de **similitud** (rotación + escala uniforme +
traslación) en lugar de una homografía completa: es la que produce un
escáner, tiene menos grados de libertad y, por tanto, estimaciones mucho
más estables entre bitácoras del mismo lote.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Tuple

import cv2
import numpy as np
from loguru import logger

from app.core.config import AppConfig
from app.vision.preprocessing import to_gray

MIN_INLIERS = 25
MAX_ROTATION_DEG = 1.5
MAX_SCALE_DRIFT = 0.02
MAX_TRANSLATION_PX = 40.0


@dataclass
class TransformResult:
    """Transformación de similitud página → referencia.

    Mapea coordenadas de la página al sistema de la referencia:
    [s·cosθ, −s·sinθ, tx; s·sinθ, s·cosθ, ty]
    """

    rot: float = 0.0
    tx: float = 0.0
    ty: float = 0.0
    scale: float = 1.0
    inliers: int = 0
    reliable: bool = True


def compute_similarity_transform(
    page: np.ndarray, template: np.ndarray, config: AppConfig
) -> TransformResult:
    """Estima la similitud (rot + escala uniforme + traslación) página→referencia.

    Guardarraíles: si hay pocos inliers o los parámetros son absurdos
    (rotación/escala/traslación fuera de rango de escáner), la página se
    marca ``reliable=False`` para que el ancla por lote tome el control.
    """
    if page.shape[:2] != template.shape[:2]:
        page = cv2.resize(
            page, (template.shape[1], template.shape[0]),
            interpolation=cv2.INTER_LINEAR,
        )

    gray_page = to_gray(page)
    gray_tpl = to_gray(template)

    orb = cv2.ORB_create(nfeatures=4000, fastThreshold=15)
    kp_tpl, des_tpl = orb.detectAndCompute(gray_tpl, None)
    kp_page, des_page = orb.detectAndCompute(gray_page, None)

    if des_tpl is None or des_page is None:
        logger.warning("Alineación: sin características detectadas")
        return TransformResult(reliable=False)

    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
    matches = matcher.knnMatch(des_tpl, des_page, k=2)
    good = [
        m for m, n in matches
        if m.distance < 0.75 * n.distance
    ]
    if len(good) < config.min_match_count:
        logger.warning(
            f"Alineación: solo {len(good)} coincidencias "
            f"(mínimo {config.min_match_count})"
        )
        return TransformResult(reliable=False)

    src_pts = np.float32([kp_tpl[m.queryIdx].pt for m in good])
    dst_pts = np.float32([kp_page[m.trainIdx].pt for m in good])
    matrix, mask = cv2.estimateAffinePartial2D(
        dst_pts, src_pts, method=cv2.RANSAC, ransacReprojThreshold=5.0,
    )
    if matrix is None:
        logger.warning("Alineación: no se pudo estimar la similitud")
        return TransformResult(reliable=False)

    rot = math.degrees(math.atan2(matrix[1, 0], matrix[0, 0]))
    scale = math.sqrt(matrix[0, 0] ** 2 + matrix[1, 0] ** 2)
    tx, ty = float(matrix[0, 2]), float(matrix[1, 2])
    inliers = int(mask.sum()) if mask is not None else 0

    reliable = (
        inliers >= MIN_INLIERS
        and abs(rot) <= MAX_ROTATION_DEG
        and abs(scale - 1.0) <= MAX_SCALE_DRIFT
        and abs(tx) <= MAX_TRANSLATION_PX
        and abs(ty) <= MAX_TRANSLATION_PX
    )
    logger.debug(f"Alineación: rot={rot:.3f} tx={tx:.2f} ty={ty:.2f} "
                 f"scale={scale:.4f} inliers={inliers} reliable={reliable}")
    return TransformResult(rot=rot, tx=tx, ty=ty, scale=scale,
                           inliers=inliers, reliable=reliable)


def apply_transform(image: np.ndarray, transform: TransformResult) -> np.ndarray:
    """Deforma ``image`` aplicando la transformación de similitud."""
    if (transform.tx == 0 and transform.ty == 0
            and transform.rot == 0 and transform.scale == 1):
        return image
    height, width = image.shape[:2]
    return warp_with_transform(image, transform, (width, height))


def warp_with_transform(
    image: np.ndarray, transform: TransformResult,
    size: Tuple[int, int],
) -> np.ndarray:
    """Deforma ``image`` al lienzo ``size`` (width, height).

    Usa el mismo tipo de transformación que ``apply_transform`` pero con
    un lienzo de salida explícito (p. ej. el marco de la referencia), para
    que varias páginas queden en un mismo sistema de coordenadas.
    """
    height, width = image.shape[:2]
    rad = math.radians(transform.rot)
    c, s = math.cos(rad), math.sin(rad)
    matrix = np.array([
        [transform.scale * c, -transform.scale * s, transform.tx],
        [transform.scale * s,  transform.scale * c, transform.ty],
    ], dtype=np.float32)
    return cv2.warpAffine(
        image, matrix, size,
        flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT,
        borderValue=(255, 255, 255),
    )


def align_to_template(
    page: np.ndarray, template: np.ndarray, config: AppConfig
) -> Tuple[np.ndarray, str]:
    """Alinea ``page`` al sistema de coordenadas de ``template`` (fallback).

    Returns:
        (imagen alineada, calidad de alineación: "ok" | "low")
    """
    transform = compute_similarity_transform(page, template, config)
    aligned = apply_transform(page, transform)
    quality = "ok" if transform.reliable else "low"
    if not transform.reliable:
        logger.warning("Alineación: estimación no confiable, se aplica "
                       "de todos modos")
    return aligned, quality

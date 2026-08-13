"""Alineación automática de páginas contra una imagen de referencia.

Soporta rotación, traslación y pequeñas diferencias de escala mediante
coincidencia de características ORB/AKAZE + ajuste de similitud (RANSAC).
Cuando no hay textura suficiente para las características, usa correlación
de fase como fallback de traslación.

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
MAX_TRANSLATION_RATIO = 0.02
MIN_PHASE_RESPONSE = 0.12


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
    method: str = "none"
    score: float = 0.0


def scale_transform_for_shape(
    transform: TransformResult,
    source_shape: Tuple[int, ...],
    target_shape: Tuple[int, ...],
) -> TransformResult:
    """Escala una transformación entre resoluciones del mismo documento.

    ``compute_similarity_transform`` trabaja en el lienzo de la imagen
    principal. Las regiones de fecha pueden renderizarse a otro DPI; en ese
    caso la misma transformación conserva el ángulo y la escala, pero sus
    traslaciones deben expresarse en píxeles del nuevo lienzo.
    """
    if len(source_shape) < 2 or len(target_shape) < 2:
        return transform
    source_height, source_width = source_shape[:2]
    target_height, target_width = target_shape[:2]
    if source_width <= 0 or source_height <= 0:
        return transform
    scale_x = target_width / source_width
    scale_y = target_height / source_height
    return TransformResult(
        rot=transform.rot,
        tx=transform.tx * scale_x,
        ty=transform.ty * scale_y,
        scale=transform.scale,
        inliers=transform.inliers,
        reliable=transform.reliable,
        method=transform.method,
        score=transform.score,
    )


def _max_translation(shape: Tuple[int, ...]) -> float:
    """Límite de traslación compatible con la resolución de la imagen."""
    if len(shape) < 2:
        return MAX_TRANSLATION_PX
    return max(MAX_TRANSLATION_PX, min(shape[:2]) * MAX_TRANSLATION_RATIO)


def _result_from_matrix(
    matrix: np.ndarray,
    mask: Optional[np.ndarray],
    shape: Tuple[int, ...],
    method: str,
    score: float = 0.0,
) -> TransformResult:
    """Convierte una matriz OpenCV en resultado y aplica sus guardarraíles."""
    rot = math.degrees(math.atan2(matrix[1, 0], matrix[0, 0]))
    scale = math.sqrt(matrix[0, 0] ** 2 + matrix[1, 0] ** 2)
    tx, ty = float(matrix[0, 2]), float(matrix[1, 2])
    inliers = int(mask.sum()) if mask is not None else 0
    reliable = (
        inliers >= MIN_INLIERS
        and abs(rot) <= MAX_ROTATION_DEG
        and abs(scale - 1.0) <= MAX_SCALE_DRIFT
        and abs(tx) <= _max_translation(shape)
        and abs(ty) <= _max_translation(shape)
    )
    return TransformResult(
        rot=rot,
        tx=tx,
        ty=ty,
        scale=scale,
        inliers=inliers,
        reliable=reliable,
        method=method,
        score=score,
    )


def _feature_transform(
    gray_page: np.ndarray,
    gray_template: np.ndarray,
    config: AppConfig,
    method: str,
) -> Optional[TransformResult]:
    """Estima una similitud con ORB o AKAZE."""
    if method == "orb":
        detector = cv2.ORB_create(nfeatures=4000, fastThreshold=15)
        ratio = 0.75
    else:
        detector = cv2.AKAZE_create()
        ratio = 0.80

    kp_tpl, des_tpl = detector.detectAndCompute(gray_template, None)
    kp_page, des_page = detector.detectAndCompute(gray_page, None)
    if des_tpl is None or des_page is None:
        return None

    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
    matches = matcher.knnMatch(des_tpl, des_page, k=2)
    good = [
        pair[0] for pair in matches
        if len(pair) >= 2 and pair[0].distance < ratio * pair[1].distance
    ]
    if len(good) < config.min_match_count:
        return None

    src_pts = np.float32([kp_tpl[m.queryIdx].pt for m in good])
    dst_pts = np.float32([kp_page[m.trainIdx].pt for m in good])
    matrix, mask = cv2.estimateAffinePartial2D(
        dst_pts,
        src_pts,
        method=cv2.RANSAC,
        ransacReprojThreshold=5.0,
    )
    if matrix is None:
        return None
    return _result_from_matrix(
        matrix,
        mask,
        gray_page.shape,
        method,
        score=(int(mask.sum()) / len(good)) if mask is not None else 0.0,
    )


def _phase_transform(
    gray_page: np.ndarray, gray_template: np.ndarray
) -> Optional[TransformResult]:
    """Fallback de traslación para páginas con poca textura local."""
    if gray_page.shape != gray_template.shape:
        return None
    height, width = gray_page.shape[:2]
    if width < 16 or height < 16:
        return None
    try:
        window = cv2.createHanningWindow((width, height), cv2.CV_32F)
        page_float = gray_page.astype(np.float32)
        template_float = gray_template.astype(np.float32)
        shift, response = cv2.phaseCorrelate(
            page_float, template_float, window
        )
    except cv2.error as exc:
        logger.debug(f"Alineación por fase no disponible: {exc}")
        return None

    tx, ty = float(shift[0]), float(shift[1])
    response = float(response)
    reliable = (
        math.isfinite(tx)
        and math.isfinite(ty)
        and math.isfinite(response)
        and response >= MIN_PHASE_RESPONSE
        and abs(tx) <= _max_translation(gray_page.shape)
        and abs(ty) <= _max_translation(gray_page.shape)
    )
    return TransformResult(
        tx=tx,
        ty=ty,
        inliers=0,
        reliable=reliable,
        method="phase",
        score=response,
    )


def compute_similarity_transform(
    page: np.ndarray, template: np.ndarray, config: AppConfig
) -> TransformResult:
    """Estima la similitud (rot + escala uniforme + traslación) página→referencia.

    Guardarraíles: si hay pocos inliers o los parámetros son absurdos
    (rotación/escala/traslación fuera de rango de escáner), la página se
    marca ``reliable=False`` para que el ancla por lote tome el control.
    AKAZE y la correlación de fase solo se prueban cuando ORB no es fiable.
    """
    if page.shape[:2] != template.shape[:2]:
        page = cv2.resize(
            page, (template.shape[1], template.shape[0]),
            interpolation=cv2.INTER_LINEAR,
        )

    gray_page = to_gray(page)
    gray_tpl = to_gray(template)
    candidates: List[TransformResult] = []

    for method in ("orb", "akaze"):
        try:
            candidate = _feature_transform(
                gray_page, gray_tpl, config, method
            )
        except cv2.error as exc:
            logger.debug(f"Alineación {method} falló: {exc}")
            candidate = None
        if candidate is not None:
            candidates.append(candidate)
            if candidate.reliable:
                break

    phase = None
    if not any(candidate.reliable for candidate in candidates):
        phase = _phase_transform(gray_page, gray_tpl)
        if phase is not None and phase.reliable:
            candidates.append(phase)

    reliable = [candidate for candidate in candidates if candidate.reliable]
    if reliable:
        result = max(
            reliable,
            key=lambda candidate: (candidate.inliers, candidate.score),
        )
    elif candidates:
        result = max(
            candidates,
            key=lambda candidate: (candidate.inliers, candidate.score),
        )
    elif phase is not None:
        result = phase
    else:
        result = TransformResult(reliable=False, method="none")

    logger.debug(
        f"Alineación ({result.method}): rot={result.rot:.3f} "
        f"tx={result.tx:.2f} ty={result.ty:.2f} "
        f"scale={result.scale:.4f} inliers={result.inliers} "
        f"score={result.score:.3f} reliable={result.reliable}"
    )
    return result


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
    aligned = apply_transform(page, transform) if transform.reliable else page
    quality = "ok" if transform.reliable else "low"
    if not transform.reliable:
        logger.warning("Alineación: estimación no confiable, se conserva "
                       "la imagen original")
    return aligned, quality

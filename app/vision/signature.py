"""Detección de firmas: solo presencia/ausencia de escritura.

Usa dos señales complementarias dentro de una banda central de la región:

1. **Tinta texturizada**: píxeles oscuros con gradiente local alto (los
   trazos manuscritos tienen borde; el fondo gris de una fotocopia o un
   relleno plano no aporta gradiente).
2. **Trazos significativos**: componentes conexas con área >=
   ``min_component_area`` tras una apertura morfológica que elimina los
   speckles de ruido del escaneo.

El umbral de oscuridad es adaptativo (Otsu sobre la banda) con respaldo
en ``texture_threshold`` cuando la banda es casi uniforme, para ser
robusto a fotocopias más claras u oscuras que la referencia.

La decisión tiene tres estados:

- ``true``: tinta texturizada con trazos significativos (>= ``min_components``
  componentes de área >= ``min_component_area``); o tinta texturizada fuerte
  (>= 2.5x ``min_texture_ink``) con un trazo dominante de área >=
  ``min_dominant_area`` (cubre firmas que se fusionan con la línea impresa
  del campo).
- ``false``: ninguna señal (región limpia), o la banda está
  oscura/deteriorada (``ink_ratio`` > ``max_ink_ratio``) donde una firma
  no es distinguible y por robustez se considera ausente.
- ``unclear`` (WARNING): evidencia parcial (solo una señal, p. ej. texto
  impreso dentro de la banda o trazos por debajo del umbral). No se acusa
  una falta automáticamente: la clasificación de discrepancias lo marca
  como REVISAR.

La confianza acompaña a la evidencia, no a la etiqueta:

- **Presente** (``true``): escala de 0.30 a 1.0 con el margen de tinta
  sobre ``min_texture_ink`` (1.0 recién al alcanzar 30x el umbral). Una
  detección marginal queda por debajo de ``sig_present_conf`` y la
  clasificación de discrepancias la marca como INCIERTA en vez de
  confiarla ciegamente.
- **Ausente** (``false``) en región limpia: escala de 0.45 a 0.95 según
  qué tan lejos de los umbrales quedaron la tinta y los trazos; una
  ausencia dudosa queda por debajo de ``sig_absent_conf`` y también se
  marca como INCIERTA.
"""

from __future__ import annotations

from typing import Optional

import cv2
import numpy as np

from app.models.schemas import FieldResult, Status
from app.templates.schema import FieldTemplate

# Estado de evidencia parcial: presente en el JSON como "unclear".
UNCLEAR = "unclear"


def _central_band(region: np.ndarray, field: FieldTemplate) -> np.ndarray:
    """Banda central de la región en gris suavizado.

    Evita bordes del recorte y etiquetas impresas que rozan la parte
    superior/inferior de la región.
    """
    gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY) if region.ndim == 3 else region
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    height, width = gray.shape
    top = int(height * field.zone_top)
    bottom = max(top + 1, int(height * field.zone_bottom))
    return gray[top:bottom]


def _dark_threshold(band: np.ndarray, fallback: float) -> float:
    """Umbral de oscuridad adaptativo (Otsu) con respaldo fijo.

    Devuelve el umbral Otsu si es razonable (30-230); en bandas casi
    uniformes (blancas o negras) Otsu degenera y se usa ``fallback``.
    """
    otsu, _ = cv2.threshold(
        np.uint8(band), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
    )
    if 30 <= int(otsu) <= 230:
        return float(otsu)
    return float(fallback)


def _textured_ink(band: np.ndarray, thr: float) -> float:
    """Fracción de píxeles oscuros (< thr) con gradiente local > 30.

    El gradiente se calcula con diferencias centrales (np.gradient) sobre
    la banda suavizada: es poco sensible al ruido plano de las fotocopias
    grises y solo responde a los trazos de escritura.
    """
    dark = band < thr
    if not dark.any():
        return 0.0
    blurred = band.astype(np.float32)
    gy, gx = np.gradient(blurred)
    mag = np.sqrt(gx * gx + gy * gy)
    return float(np.mean(dark & (mag > 30.0)))


def _suppress_printed_horizontal_line(
    band: np.ndarray, thr: float
) -> tuple[np.ndarray, float]:
    """Elimina líneas impresas largas antes de puntuar la escritura.

    Los campos de firma/licencia contienen una línea horizontal de base. Una
    apertura con un kernel proporcional al ancho conserva únicamente trazos
    casi horizontales y largos; después se blanquea una banda estrecha a su
    alrededor. Los trazos manuscritos verticales, curvas y rúbricas cortas se
    mantienen y siguen aportando evidencia.

    Devuelve la banda limpia y la fracción de píxeles identificada como línea
    impresa, útil para explicar la decisión en el reporte.
    """
    if band.size == 0:
        return band, 0.0
    binary = np.uint8(band < thr) * 255
    width = binary.shape[1]
    kernel_width = max(15, int(round(width * 0.35)))
    horizontal = cv2.morphologyEx(
        binary,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_width, 1)),
    )
    # La impresión puede ocupar 2-3 píxeles por el antialiasing/escaneo.
    horizontal = cv2.dilate(
        horizontal,
        cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)),
        iterations=1,
    )
    mask = horizontal > 0
    if not mask.any():
        return band, 0.0
    cleaned = band.copy()
    cleaned[mask] = 255
    return cleaned, float(np.mean(mask))


def _pen_ink(band_color: np.ndarray, thr_gray: float) -> float:
    """Fracción de tinta de bolígrafo (color) dentro de la banda.

    La tinta de bolígrafo casi nunca es neutra: azul y otros colores con
    saturación alta que el canal gris puede aplanar (una firma azul clara
    sobre fotocopia gris). Se miden los píxeles:

    - oscuros en el canal de grises (``< thr_gray``): un trazado real;
    - con saturación alta (>= 70): no es gris del fondo ni ruido de
      escaneo;
    - con brillo medio ([20, 230]): no es marca de agua ni reflejo.

    Devuelve la fracción de esos píxeles respecto a la banda (0 si la
    banda no tiene canal de color). Es una señal complementaria a la
    textura en gris: sufre la misma escala en min_texture_ink/``2.5x``.
    """
    if band_color is None or band_color.ndim != 3:
        return 0.0
    hsv = cv2.cvtColor(band_color, cv2.COLOR_BGR2HSV)
    saturation = hsv[:, :, 1]
    value = hsv[:, :, 2]
    gray = cv2.cvtColor(band_color, cv2.COLOR_BGR2GRAY)
    mask = (
        (gray < thr_gray)
        & (saturation >= 70)
        & (value >= 20)
        & (value <= 200)
    )
    return float(np.mean(mask)) if mask.size else 0.0


def _significant_strokes(band: np.ndarray, min_area: float) -> tuple[int, float]:
    """Número de trazos significativos y área del mayor (px).

    Binariza la banda con Otsu, aplica apertura morfológica elíptica 5x5
    (elimina speckles aislados) y cuenta componentes conexas con área >=
    ``min_area``.
    """
    _, binary = cv2.threshold(
        np.uint8(band), 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
    )
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    opened = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
    num_labels, _, stats, _ = cv2.connectedComponentsWithStats(
        opened, connectivity=8
    )
    areas = [
        stats[i][cv2.CC_STAT_AREA]
        for i in range(1, num_labels)
        if stats[i][cv2.CC_STAT_AREA] >= min_area
    ]
    return len(areas), float(max(areas, default=0.0))


def detect_signature(
    region: np.ndarray, field: FieldTemplate, page_number: int
) -> FieldResult:
    """Determina si hay una firma (escritura) en la región.

    Criterios (configurables por campo en la plantilla):
    - Banda central [``zone_top``, ``zone_bottom``) del alto del recorte.
    - Umbral adaptativo (Otsu) con respaldo en ``texture_threshold``.
    - Tinta texturizada en fracción >= ``min_texture_ink``.
    - Trazos significativos >= ``min_components`` con área >=
      ``min_component_area``.
    - Guarda: si la banda es oscura en más de ``max_ink_ratio`` se considera
      región dañada/oscura y por tanto ausente (sin confianza suficiente
      para afirmar que hay una firma).

    Returns:
        FieldResult con valor "true", "false" o "unclear" y su confianza.
    """
    band = _central_band(region, field)
    thr = _dark_threshold(band, field.texture_threshold)
    ink_ratio = float(np.mean(band < thr))

    if ink_ratio > field.max_ink_ratio:
        # Región oscura/deteriorada: no se distingue una firma. Se trata
        # como ausente pero con confianza mínima (no se puede afirmar).
        return FieldResult(
            page_number=page_number,
            field_id=field.id,
            field_type=field.type.value,
            source="vision",
            value="false",
            confidence=round(min(0.15, ink_ratio), 3),
            status=Status.ERROR if field.required else Status.WARNING,
            comment=(
                f"Firma ausente: la región está oscura (tinta={ink_ratio:.3f} "
                f"> {field.max_ink_ratio:.3f}) y no muestra trazos de escritura"
            ),
        )

    analysis_band, printed_line_ratio = _suppress_printed_horizontal_line(
        band, thr
    )
    textured = _textured_ink(analysis_band, thr)
    pen = _pen_ink(_colored_band(region, field), thr)
    ink_evidence = max(textured, pen)
    strokes, max_area = _significant_strokes(
        analysis_band, field.min_component_area
    )

    strong = ink_evidence >= field.min_texture_ink
    strokes_ok = strokes >= field.min_components
    # Trazo dominante con tinta texturizada/color fuerte: cubre firmas cuyos
    # trazos se fusionan con la línea impresa del campo en un solo
    # componente, o la tinta de pluma con poca textura en gris (2.5x el
    # umbral exige escritura real, no una línea fina).
    dominant_ok = (
        max_area >= field.min_dominant_area
        and ink_evidence >= 2.5 * field.min_texture_ink
    )

    if strong and (strokes_ok or dominant_ok):
        evidence_ratio = ink_evidence / max(field.min_texture_ink, 1e-6)
        # La detección empieza en el umbral, pero eso no equivale a certeza:
        # la confianza llega a 1.0 recién al alcanzar 30x la evidencia mínima.
        evidence_margin = min(1.0, max(0.0, (evidence_ratio - 1.0) / 29.0))
        confidence = round(0.30 + 0.70 * evidence_margin, 3)
        return FieldResult(
            page_number=page_number,
            field_id=field.id,
            field_type=field.type.value,
            source="vision",
            value="true",
            confidence=confidence,
            status=Status.OK,
            comment=(
                f"Firma detectada: {strokes} trazo(s) (mayor de {max_area:.0f} px), "
                f"tinta texturizada={textured:.4f} y/o pluma(color)="
                f"{pen:.4f} >= {field.min_texture_ink:.3f}; línea impresa "
                f"suprimida={printed_line_ratio:.4f}"
            ),
        )

    if strong or strokes_ok or dominant_ok:
        # Evidencia parcial: p. ej. texto impreso o trazos débiles. No se
        # afirma ni la presencia ni la ausencia.
        confidence = round(
            min(0.5, 0.2 + 0.3 * max(ink_evidence / max(field.min_texture_ink, 1e-6),
                                     strokes / max(field.min_components, 1))),
            3,
        )
        return FieldResult(
            page_number=page_number,
            field_id=field.id,
            field_type=field.type.value,
            source="vision",
            value=UNCLEAR,
            confidence=confidence,
            status=Status.WARNING,
            comment=(
                f"Firma incierta: evidencia parcial (tinta texturizada="
                f"{textured:.4f}, pluma={pen:.4f}, trazos={strokes}); "
                f"línea impresa suprimida={printed_line_ratio:.4f}; revisar "
                f"manualmente"
            ),
        )

    texture_evidence = min(
        1.0, ink_evidence / max(field.min_texture_ink, 1e-6)
    )
    stroke_evidence = min(
        1.0, strokes / max(field.min_components, 1)
    )
    # Si cualquiera de las señales se acerca al umbral, la ausencia es menos
    # confiable y debe quedar disponible para revisión manual.
    evidence = max(texture_evidence, stroke_evidence)
    confidence = round(0.45 + 0.50 * (1.0 - evidence), 3)

    return FieldResult(
        page_number=page_number,
        field_id=field.id,
        field_type=field.type.value,
        source="vision",
        value="false",
        confidence=confidence,
        status=Status.ERROR if field.required else Status.WARNING,
        comment=(
            f"Firma ausente: el campo está vacío, no se detectaron trazos "
            f"de escritura (tinta texturizada={textured:.4f} < "
            f"{field.min_texture_ink:.3f}, pluma={pen:.4f}, trazos={strokes}, "
            f"línea impresa suprimida={printed_line_ratio:.4f})"
        ),
    )


def _colored_band(region: np.ndarray, field: FieldTemplate) -> Optional[np.ndarray]:
    """Banda central en BGR (sin suavizar) para la señal de pluma, o None
    si la región no tiene canal de color."""
    if region.ndim != 3:
        return None
    height, width = region.shape[:2]
    top = int(height * field.zone_top)
    bottom = max(top + 1, int(height * field.zone_bottom))
    return region[top:bottom]

"""Detección de checkboxes (marcado/vacío)."""

from __future__ import annotations

import numpy as np

from app.models.schemas import FieldResult, Status
from app.templates.schema import FieldTemplate
from app.vision.marks import analyze_ink


def detect_checkbox(
    region: np.ndarray, field: FieldTemplate, page_number: int
) -> FieldResult:
    """Determina si una casilla está marcada por cobertura de tinta.

    Args:
        region: Región recortada de la casilla.
        field: Definición del campo checkbox.
        page_number: Página procesada.

    Returns:
        FieldResult con valor "marcado" o "vacio".
    """
    analysis = analyze_ink(region)
    marked = (
        field.min_ink_ratio <= analysis.ink_ratio <= field.max_ink_ratio
    )

    value = "marked" if marked else "empty"
    confidence = min(
        1.0, abs(analysis.ink_ratio - field.min_ink_ratio) / 0.1 + 0.5
    )
    confidence = round(confidence, 3)

    if marked:
        status = Status.OK
        comment = f"tinta={analysis.ink_ratio:.3f}"
    else:
        status = Status.ERROR if field.required else Status.WARNING
        comment = f"tinta={analysis.ink_ratio:.3f}"

    return FieldResult(
        page_number=page_number,
        field_id=field.id,
        field_type=field.type.value,
        source="vision",
        value=value,
        confidence=confidence,
        status=status,
        comment=comment,
    )

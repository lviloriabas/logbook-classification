"""Verificación final de un lote de bitácoras.

Un libro (PDF) pertenece a una sola aeronave, por lo que la matrícula debe
ser la misma en todas sus páginas. Este paso, ejecutado al final del
procesamiento, determina lógicamente la matrícula correcta de cada libro
(voto mayoritario entre los valores que cumplen el patrón) y corrige los
valores de las páginas discrepantes o ilegibles.

La corrección escribe el valor corregido en el campo ``matricula`` de cada
página y marca ``page.discrepancy = True`` en las páginas que no coincidían
con la matrícula del libro.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import List, Optional

from loguru import logger

from app.models.schemas import PageResult, Status, ValidationReport
from app.templates.schema import Template


def verify_matriculas(
    reports: List[ValidationReport], template: Template
) -> int:
    """Corrige las matrículas por libro y marca las discrepancias.

    Args:
        reports: Reportes de validación (uno por PDF procesado).
        template: Plantilla usada (aporta la regex de ``matricula``).

    Returns:
        Número de páginas marcadas como discrepancia.
    """
    mat_field = template.field("matricula")
    pattern = (
        re.compile(mat_field.regex) if mat_field and mat_field.regex else None
    )

    discrepancies = 0
    for report in reports:
        per_page: List[PageResult] = []
        for page in report.pages:
            page.discrepancy = False
            per_page.append(page)

        values: List[Optional[str]] = []
        for page in per_page:
            result = _matricula_result(page)
            value = result.value if result is not None else None
            values.append(value)

        majority = _book_matricula(values, pattern)

        for page, value in zip(per_page, values):
            if majority is None or value != majority:
                page.discrepancy = True
                discrepancies += 1
                if majority is not None:
                    result = _matricula_result(page)
                    if result is not None and result.value != majority:
                        original = result.value
                        logger.info(
                            f"[Verification] {Path(report.pdf_path).name} "
                            f"page {page.page_number}: registration corrected "
                            f"{original!r} -> {majority!r}"
                        )
                        result.value = majority
                        result.status = Status.ERROR
                        result.comment = (
                            f"corrected from {original!r} by majority vote"
                        )

        if majority is not None:
            support = sum(1 for v in values if v == majority)
            total_valid = len(values)
            logger.info(
                f"[Verification] {Path(report.pdf_path).name}: "
                f"book registration = {majority} "
                f"({support}/{len(values)} pages, "
                f"{sum(1 for p in per_page if p.discrepancy)} "
                f"discrepancy(-ies))"
            )
            if support < max(2, total_valid // 2):
                logger.warning(
                    f"[Verification] {Path(report.pdf_path).name}: majority "
                    f"holds only {support}/{len(values)} pages; the book may "
                    f"contain multiple aircraft or heavy OCR errors"
                )
        else:
            logger.warning(
                f"[Verification] {Path(report.pdf_path).name}: "
                f"could not determine the registration (no valid value)"
            )
    return discrepancies


def _matricula_result(page: PageResult):
    for field in page.fields:
        if field.field_id == "matricula":
            return field
    return None


def _book_matricula(
    values: List[Optional[str]], pattern: Optional[re.Pattern]
) -> Optional[str]:
    """Matrícula del libro por voto mayoritario de valores válidos."""
    valid = [v for v in values if v and (pattern is None or pattern.match(v))]
    if not valid:
        return None
    counter = Counter(valid)
    ordered = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    return ordered[0][0]

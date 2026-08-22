"""Deteccion de paginas de bitacora repetidas dentro de un batch."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from app.models.schemas import ValidationReport
from app.validation.grouping import log_number


@dataclass(frozen=True)
class DuplicateLogPage:
    """Resultado de duplicidad para una pagina, en el orden del batch."""

    pdf_path: str
    page_number: int
    log_number: int | None
    duplicate: bool


def detect_duplicate_log_pages(
    reports: Sequence[ValidationReport],
) -> list[DuplicateLogPage]:
    """Marca apariciones posteriores de un mismo ``log_number`` valido.

    La primera aparicion queda con ``duplicate=False``. Los valores ausentes
    o que no tengan exactamente siete digitos tampoco se consideran
    duplicados.
    """
    seen: set[int] = set()
    detected: list[DuplicateLogPage] = []
    for report in reports:
        for page in report.pages:
            number = log_number(page)
            duplicate = number is not None and number in seen
            detected.append(
                DuplicateLogPage(
                    pdf_path=report.pdf_path,
                    page_number=page.page_number,
                    log_number=number,
                    duplicate=duplicate,
                )
            )
            if number is not None:
                seen.add(number)
    return detected

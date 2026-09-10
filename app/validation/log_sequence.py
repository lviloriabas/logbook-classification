"""Número de bitácora que las páginas vecinas del PDF no dejan en duda.

Sin ``log_number`` la página no tiene libro, así que tampoco hereda la
matrícula ni la fecha del libro, y AirVault la recibe con un obligatorio
vacío: va entera a REVISAR aunque el resto se haya leído bien. Casi siempre
es un número tachado, borroso o cortado en una página cuyo sitio en el libro
está a la vista.

El orden del PDF no ordena los libros (``app.validation.grouping``), pero sí
cuenta como evidencia en un caso: cuando las páginas legibles de un lado y
del otro son del mismo libro y entre ellas faltan exactamente tantos números
como páginas sin leer hay. ``2147310``, ilegible, ``2147312`` solo admite
``2147311``. No se deduce nada si el hueco no cuadra, si cruza de libro, si
el número ya está en la ejecución o si los dígitos que el OCR sí sacó se
parecen poco al número deducido.
"""

from __future__ import annotations

from difflib import SequenceMatcher
import re
from typing import List, Optional, Sequence

from loguru import logger

from app.models.schemas import FieldResult, PageResult, Status, ValidationReport
from app.validation.grouping import log_number
from app.validation.page_status import recompute_page_status

LOG_NUMBER_FIELD_ID = "log_number"
INFERENCE_METHOD = "log_number_pdf_sequence"
# Confianza de una deducción: por debajo de una lectura directa buena.
_INFERRED_CONFIDENCE = 0.7
# Con cuatro dígitos leídos ya hay algo que contrastar; con menos, el OCR no
# dice nada del número.
_MIN_RAW_DIGITS = 4
_MIN_RAW_SIMILARITY = 0.6


def _log_field(page: PageResult) -> Optional[FieldResult]:
    for field in page.fields:
        if field.field_id == LOG_NUMBER_FIELD_ID:
            return field
    return None


def _same_book(left: int, right: int) -> bool:
    """Misma serie y misma mitad (00-49 o 50-99)."""
    return (
        left // 100 == right // 100
        and (left % 100 < 50) == (right % 100 < 50)
    )


def _compatible(field: FieldResult, candidate: int) -> bool:
    """Los dígitos que el OCR sí sacó no contradicen el número deducido."""
    digits = re.sub(r"\D", "", field.raw_value or field.value or "")
    if len(digits) < _MIN_RAW_DIGITS:
        return True
    similarity = SequenceMatcher(None, digits, f"{candidate:07d}").ratio()
    return similarity >= _MIN_RAW_SIMILARITY


def _assign(
    page: PageResult, field: FieldResult, candidate: int,
    before: int, after: int,
) -> None:
    previous = field.value
    if previous and previous not in field.alternatives:
        field.alternatives.append(previous)
    field.value = f"{candidate:07d}"
    field.confidence = _INFERRED_CONFIDENCE
    field.status = Status.WARNING
    field.source = "inferred"
    field.inference_method = INFERENCE_METHOD
    field.votes = 2
    note = (
        f"Inferred from neighbouring PDF pages {before:07d} and {after:07d}"
    )
    field.comment = f"{field.comment} | {note}" if field.comment else note
    recompute_page_status(page)


def infer_log_numbers_from_pdf_order(
    reports: Sequence[ValidationReport],
) -> int:
    """Completa los ``log_number`` que sus vecinas del PDF encierran.

    Va antes de los correctores por libro, para que la página ya deducida
    entre en su libro y reciba de él matrícula y fecha. Devuelve cuántos
    números se dedujeron.
    """
    known = {
        number
        for report in reports
        for page in report.pages
        if (number := log_number(page)) is not None
    }
    inferred = 0
    for report in reports:
        pages = sorted(report.pages, key=lambda page: page.page_number)
        before: Optional[int] = None
        pending: List[PageResult] = []
        for index, page in enumerate(pages):
            if index and page.page_number != pages[index - 1].page_number + 1:
                # Un hueco en el rango procesado: ya no son vecinas.
                before, pending = None, []
            if page.blank:
                continue
            number = log_number(page)
            if number is None:
                if before is not None and _log_field(page) is not None:
                    pending.append(page)
                else:
                    before, pending = None, []
                continue
            if (
                before is not None
                and pending
                and number - before == len(pending) + 1
                and _same_book(before, number)
            ):
                candidates = [
                    before + offset for offset in range(1, len(pending) + 1)
                ]
                if all(
                    candidate not in known
                    and _compatible(_log_field(missing), candidate)
                    for missing, candidate in zip(pending, candidates)
                ):
                    for missing, candidate in zip(pending, candidates):
                        _assign(
                            missing, _log_field(missing), candidate,
                            before, number,
                        )
                        known.add(candidate)
                        inferred += 1
            before, pending = number, []
    if inferred:
        logger.info(
            f"[Libro] {inferred} número(s) de bitácora deducidos de sus "
            "páginas vecinas del PDF"
        )
    return inferred

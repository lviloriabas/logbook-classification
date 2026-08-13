"""Corrector de matrículas por libro (un avión por libro).

Un "libro" es un bloque de 50 páginas (misma serie del ``log_number`` y
misma mitad del logpage 00-49/50-99). Como cada libro pertenece a una
sola aeronave, la matrícula debe ser idéntica en todas sus páginas.

Después del procesamiento OCR y de la normalización de formato, este
corrector:

1. Agrupa las páginas en libros (regla de la serie + mitad).
2. En cada libro, vota la matrícula dominante entre las lecturas
   fiables (formato OK y confianza >= umbral).
3. Corrige de forma agresiva a la dominante TODAS las páginas que no
   coincidan (ilegibles, de confianza baja o de formato válido pero
   distinto), dejando el valor original en el comentario para
   auditoría: un libro = un avión es una regla dura.
4. Si el libro no tiene ninguna lectura válida, no hay ganador y las
   páginas quedan sin matrícula (no se detectó).
"""

from __future__ import annotations

from collections import Counter
import re
from typing import Dict, List, Optional, Tuple

from loguru import logger

from app.models.schemas import PageResult, Status, ValidationReport
from app.validation.grouping import group_books

MATRICULA_FIELD_ID = "matricula"

# Confianza mínima para que una matrícula vote por la dominante.
MIN_VOTE_CONFIDENCE = 0.5

_ORDER = {Status.OK: 0, Status.WARNING: 1, Status.ERROR: 2}


def _matricula_field(page: PageResult):
    for field in page.fields:
        if field.field_id == MATRICULA_FIELD_ID:
            return field
    return None


def _book_winner(
    votes: List,
) -> Optional[Tuple[str, int, float]]:
    """Matrícula dominante del libro: (valor, número de votos, confianza).

    Gana la más frecuente; en empate, la de mayor confianza total.
    Si cada voto es de una matrícula distinta, no hay ganador.
    """
    counter: Counter = Counter()
    total_confidence: Dict[str, float] = {}
    for field in votes:
        value = field.value or ""
        counter[value] += 1
        total_confidence[value] = total_confidence.get(value, 0.0) + field.confidence

    ranked = sorted(
        counter.items(),
        key=lambda item: (item[1], total_confidence[item[0]]),
        reverse=True,
    )
    if not ranked:
        return None
    winner, count = ranked[0]
    if count == 1 and len(ranked) > 1:
        return None
    winner_confidence = total_confidence[winner] / count
    return winner, count, round(winner_confidence, 3)


def _correct_book(book: List[PageResult]) -> Tuple[int, int]:
    """Corrige las matrículas de un libro. Devuelve (corregidas, marcadas).

    Corrección agresiva: toda página cuya matrícula difiera del ganador
    (vacía, ilegible, de formato válido pero distinta) se sobrescribe con
    la matrícula del libro; el valor original queda en el comentario.
    """
    entries = [(page, _matricula_field(page)) for page in book]
    entries = [(p, f) for p, f in entries if f is not None]
    if not entries:
        return 0, 0

    votes = [
        field
        for _, field in entries
        if (field.status is Status.OK
            and field.value
            and field.confidence >= MIN_VOTE_CONFIDENCE)
    ]
    if not votes:
        return 0, 0

    winner_info = _book_winner(votes)
    if winner_info is None:
        return 0, 0
    winner, count, winner_confidence = winner_info

    corrected = 0
    flagged = 0
    for page, field in entries:
        if field.value == winner:
            continue
        original = field.value
        field.value = winner
        field.confidence = winner_confidence
        field.status = Status.OK
        field.source = "book_correction"
        field.inference_method = "book_majority"
        if original:
            field.comment = (
                f"Corrected from {original!r} by book majority "
                f"({count} vote(s))"
            )
            flagged += 1
        else:
            field.comment = (
                f"Inferred from book readings: {winner} ({count} vote(s))"
            )
            corrected += 1
        _recompute_page_status(page)

    logger.info(
        f"[Libro] Matrícula dominante {winner} ({count} votos, "
        f"conf={winner_confidence}) | corregidas: {corrected} | "
        f"discrepantes sobrescritas: {flagged}"
    )
    return corrected, flagged


def _recompute_page_status(page: PageResult) -> None:
    """Recalcula el estado de una página a partir de sus campos."""
    if not page.fields or page.blank:
        return
    # Las siete lecturas de celda son evidencia auxiliar. Su ausencia o baja
    # confianza no degrada la página si day/month/year ya quedaron resueltos.
    decisive = [
        field for field in page.fields
        if not re.fullmatch(r"(?:day|month|year)_\d", field.field_id)
    ]
    worst = max((f.status for f in decisive), key=_ORDER.get,
                default=Status.OK)
    page.status = worst


def _recompute_summary(report: ValidationReport) -> None:
    pages = report.pages
    summary = {
        "total_pages": len(pages),
        "ok_pages": 0,
        "warning_pages": 0,
        "error_pages": 0,
        "blank_pages": sum(1 for p in pages if p.blank),
    }
    for page in pages:
        if page.blank:
            continue
        if page.status is Status.OK:
            summary["ok_pages"] += 1
        elif page.status is Status.WARNING:
            summary["warning_pages"] += 1
        else:
            summary["error_pages"] += 1
    report.summary = summary


def correct_matricula_by_book(
    reports: List[ValidationReport],
) -> Dict[str, int]:
    """Corrector global de matrículas (un avión por libro).

    Args:
        reports: Reportes ya validados (uno por PDF procesado).

    Returns:
        Estadísticas: libros, corregidas, marcadas.
    """
    books = group_books(reports)
    stats = {"books": len(books), "corrected": 0, "flagged": 0}
    for book in books:
        corrected, flagged = _correct_book(book)
        stats["corrected"] += corrected
        stats["flagged"] += flagged
    for report in reports:
        _recompute_summary(report)
    logger.info(f"Corrector de matrículas: {stats}")
    return stats

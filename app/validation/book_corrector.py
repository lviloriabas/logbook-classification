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

from app.models.schemas import FieldResult, PageResult, Status, ValidationReport
from app.validation.grouping import group_books

MATRICULA_FIELD_ID = "matricula"

# Confianza mínima para que una matrícula vote por la dominante.
MIN_VOTE_CONFIDENCE = 0.5

_ORDER = {Status.OK: 0, Status.WARNING: 1, Status.ERROR: 2}
_CANONICAL_MATRICULA_RE = re.compile(r"^HP-(\d{4})(CMP|WWP)$")


def _matricula_field(page: PageResult):
    for field in page.fields:
        if field.field_id == MATRICULA_FIELD_ID:
            return field
    return None


def _handwritten_4_7_hint(field) -> Optional[str]:
    """Recupera una matrícula completa cuando el OCR leyó un 7 como ``F``.

    Esta equivalencia se limita deliberadamente al recorte manuscrito de
    matrícula. No se comparte con los campos de fecha ni transforma por sí
    sola una lectura canónica: solo aporta evidencia al consenso del libro.
    """
    if field is None or field.confidence < MIN_VOTE_CONFIDENCE:
        return None
    raw = re.sub(r"[^A-Z0-9]", "", (field.raw_value or "").upper())
    match = re.search(r"(?:HP|HR|HD)([A-Z0-9]{4})", raw)
    if match is None:
        return None
    observed = match.group(1)
    if "F" not in observed:
        return None
    number = observed.replace("F", "7")
    if not number.isdigit():
        return None
    suffix = "WWP" if "WWP" in raw else "CMP"
    return f"HP-{number}{suffix}"


def _repeated_4_read_as_7(winner: str, hint: str) -> bool:
    """Indica que el candidato solo cambia dos o más ``4`` por ``7``."""
    winner_match = _CANONICAL_MATRICULA_RE.fullmatch(winner)
    hint_match = _CANONICAL_MATRICULA_RE.fullmatch(hint)
    if winner_match is None or hint_match is None:
        return False
    if winner_match.group(2) != hint_match.group(2):
        return False
    differences = [
        (left, right)
        for left, right in zip(winner_match.group(1), hint_match.group(1))
        if left != right
    ]
    return len(differences) >= 2 and all(
        left == "4" and right == "7" for left, right in differences
    )


def _resolve_repeated_4_7_ambiguity(
    entries: List[Tuple[PageResult, FieldResult]],
    winner: str,
    winner_count: int,
) -> Optional[Tuple[str, int, float]]:
    """Prefiere 1717 frente a 1414 si el mismo libro aporta esa forma.

    Se exige una matrícula completa reconstruida desde el texto crudo, dos o
    más posiciones 4→7 y al menos tanta evidencia como votos del candidato
    1414. Así una única ``F`` aislada no cambia matrículas como 1534.
    """
    evidence: Dict[str, List[float]] = {}
    for _page, field in entries:
        hint = _handwritten_4_7_hint(field)
        if hint is None or not _repeated_4_read_as_7(winner, hint):
            continue
        evidence.setdefault(hint, []).append(float(field.confidence))
    if not evidence:
        return None
    ranked = sorted(
        evidence.items(),
        key=lambda item: (len(item[1]), sum(item[1])),
        reverse=True,
    )
    hint, confidences = ranked[0]
    if (
        len(confidences) < winner_count
        or (len(ranked) > 1 and len(ranked[1][1]) == len(confidences))
    ):
        return None
    confidence = round(sum(confidences) / len(confidences), 3)
    return hint, len(confidences), confidence


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
            and field.confidence >= MIN_VOTE_CONFIDENCE
            and field.source not in {"book_correction", "inferred"})
    ]
    if not votes:
        return 0, 0

    winner_info = _book_winner(votes)
    if winner_info is None:
        return 0, 0
    winner, count, winner_confidence = winner_info
    ambiguity = _resolve_repeated_4_7_ambiguity(entries, winner, count)
    ambiguity_from = None
    if ambiguity is not None:
        ambiguity_from = winner
        winner, count, winner_confidence = ambiguity

    corrected = 0
    flagged = 0
    for page, field in entries:
        if field.value == winner:
            continue
        original = field.value
        if original and original not in field.alternatives:
            field.alternatives.append(original)
        field.value = winner
        field.confidence = winner_confidence
        field.status = Status.OK
        field.source = "book_correction"
        field.inference_method = (
            "book_handwritten_4_7" if ambiguity_from is not None
            else "book_majority"
        )
        if ambiguity_from is not None:
            field.comment = (
                "Corrected handwritten registration 4/7 ambiguity from "
                f"{original or ambiguity_from!r} to {winner!r} using raw "
                f"evidence from the same book ({count} vote(s))"
            )
            if original:
                flagged += 1
            else:
                corrected += 1
        elif original:
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

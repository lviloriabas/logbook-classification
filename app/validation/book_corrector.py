"""Corrector de matrículas por libro (un avión por libro).

Un "libro" es un bloque de 50 páginas (misma serie del ``log_number`` y
misma mitad del logpage 00-49/50-99). Como cada libro pertenece a una
sola aeronave, la matrícula debe ser idéntica en todas sus páginas.

Después del procesamiento OCR y de la normalización de formato, este
corrector:

1. Agrupa las páginas en libros (regla de la serie + mitad).
2. En cada libro, decide la matrícula **dígito a dígito**: cada posición
   se vota por separado con todas las lecturas del libro, pesadas por su
   confianza y por lo limpia que fue la lectura.
3. Corrige de forma agresiva a la ganadora TODAS las páginas que no
   coincidan (ilegibles, de confianza baja o de formato válido pero
   distinto), dejando el valor original en el comentario para
   auditoría: un libro = un avión es una regla dura.
4. Si el libro no tiene ninguna lectura válida, no hay ganador y las
   páginas quedan sin matrícula (no se detectó).

El voto por posición sustituye a la mayoría sobre la matrícula completa.
Con la mayoría simple, una sola página confiada pero equivocada (el 7
manuscrito leído como 3) se imponía a todo un libro, porque las demás
páginas del libro leían el número de formas distintas entre sí y ninguna
alcanzaba dos votos. Separando las posiciones, esas lecturas dispersas sí
coinciden allí donde importa y el dígito correcto gana.
"""

from __future__ import annotations

from collections import defaultdict
import re
from typing import Dict, List, Optional, Tuple

from loguru import logger

from app.models.schemas import FieldResult, PageResult, Status, ValidationReport
from app.utils.postprocess import (
    AMBIGUOUS_MATRICULA_NOTE,
    WEAK_MATRICULA_NOTE,
    apply_postprocess,
)
from app.validation.grouping import group_books, log_number
from app.validation.page_status import (
    AUTO_INDEX_MIN_VOTES,
    recompute_page_status,
)

MATRICULA_FIELD_ID = "matricula"

# Peso de la evidencia según cómo se obtuvo el número: una tirada limpia de
# cuatro dígitos vale más que una reconstruida con caracteres confundibles,
# y esta mucho más que un número recompuesto con dígitos dispersos. La
# lectura reconstruida se descuenta poco: viene de una ventana anclada entre
# el prefijo y el sufijo del campo, así que es una matrícula completa, no un
# resto de texto.
_EVIDENCE_QUALITY = {
    "": 1.0,
    AMBIGUOUS_MATRICULA_NOTE: 0.8,
    WEAK_MATRICULA_NOTE: 0.25,
}
# Piso de peso: una lectura sin confianza sigue siendo evidencia, pero mínima.
_MIN_WEIGHT = 0.05
# Valores que ya son resultado de una inferencia: no pueden votarse a sí mismos.
_DERIVED_SOURCES = frozenset({"book_correction", "inferred"})
# Valores resueltos por una segunda pasada (Tesseract restringido o VLM):
# valen más que el texto crudo de la pasada principal, que ya falló.
_VERIFIED_SOURCES = frozenset({"ocr_fallback", "vlm", "fleet_validation"})

_ORDER = {Status.OK: 0, Status.WARNING: 1, Status.ERROR: 2}
_CANONICAL_MATRICULA_RE = re.compile(r"^HP-(\d{4})(CMP|WWP)$")
# El corrector no recibe ``AppConfig``. Este es el mismo umbral general que
# usa la configuración por defecto; el número de votos y los conflictos son
# las guardas adicionales que deciden si la inferencia puede ir automática.
_MIN_BOOK_AUTO_CONFIDENCE = 0.50


def _matricula_field(page: PageResult):
    for field in page.fields:
        if field.field_id == MATRICULA_FIELD_ID:
            return field
    return None


def _page_evidence(
    field: FieldResult,
) -> Optional[Tuple[str, str, float]]:
    """(número de 4 dígitos, sufijo, peso) con que una página vota.

    Se parte del texto crudo del OCR: una página descartada por el formato
    sigue conteniendo dígitos legibles, y esa evidencia vale igual que la de
    una lectura que sí pasó. El valor ya normalizado se usa cuando no hay
    texto crudo o cuando lo resolvió una segunda pasada (que vio el recorte
    de nuevo), pero nunca si viene de una corrección previa: contaría la
    inferencia anterior como si fuera una lectura nueva.
    """
    confidence = max(float(field.confidence), _MIN_WEIGHT)
    if field.raw_value and field.source not in _VERIFIED_SOURCES:
        value, note = apply_postprocess(
            field.field_id, MATRICULA_FIELD_ID, field.raw_value
        )
        match = _CANONICAL_MATRICULA_RE.fullmatch(value)
        if match is not None:
            quality = _EVIDENCE_QUALITY.get(
                note, _EVIDENCE_QUALITY[WEAK_MATRICULA_NOTE]
            )
            return match.group(1), match.group(2), confidence * quality
    if field.value and field.source not in _DERIVED_SOURCES:
        match = _CANONICAL_MATRICULA_RE.fullmatch(field.value)
        if match is not None:
            return match.group(1), match.group(2), confidence
    if field.raw_value:
        # Segunda pasada sin valor utilizable: queda el texto crudo.
        value, note = apply_postprocess(
            field.field_id, MATRICULA_FIELD_ID, field.raw_value
        )
        match = _CANONICAL_MATRICULA_RE.fullmatch(value)
        if match is not None:
            quality = _EVIDENCE_QUALITY.get(
                note, _EVIDENCE_QUALITY[WEAK_MATRICULA_NOTE]
            )
            return match.group(1), match.group(2), confidence * quality
    return None


def _unique_evidence(
    entries: List[Tuple[PageResult, FieldResult]],
) -> List[Tuple[str, str, float]]:
    """Evidencia del libro con una sola aportación por página física.

    La misma página escaneada en dos PDF distintos llega dos veces con la
    misma lectura. Contarla dos veces duplicaría un error de OCR y le daría
    ventaja sobre las páginas que solo aparecen una vez, así que de cada
    ``log_number`` se conserva la aportación de mayor peso.
    """
    best: Dict[object, Tuple[str, str, float]] = {}
    for index, (page, field) in enumerate(entries):
        evidence = _page_evidence(field)
        if evidence is None:
            continue
        # Sin log_number legible no se puede saber si dos páginas son la
        # misma, así que cada una cuenta por separado.
        key = log_number(page) or f"page-{index}"
        previous = best.get(key)
        if previous is None or evidence[2] > previous[2]:
            best[key] = evidence
    return list(best.values())


def _book_winner(
    entries: List[Tuple[PageResult, FieldResult]],
) -> Optional[Tuple[str, int, float]]:
    """Matrícula del libro por consenso dígito a dígito.

    Cada posición se decide por separado sumando el peso de todas las
    lecturas, de modo que el dígito correcto gana aunque ninguna lectura
    completa se repita. El número resultante tiene que coincidir además con
    alguna lectura completa del libro: el consenso elige entre lo que se
    leyó, nunca inventa una matrícula que no vio ninguna página. Si mezclar
    posiciones produce un número que nadie leyó, decide la lectura completa
    de más peso.

    Returns:
        (matrícula canónica, páginas que la leyeron entera, confianza) o
        None si el libro no aporta ninguna lectura utilizable.
    """
    evidence = _unique_evidence(entries)
    if not evidence:
        return None
    votes: List[Dict[str, float]] = [defaultdict(float) for _ in range(4)]
    observed: Dict[str, float] = defaultdict(float)
    suffixes: Dict[str, float] = defaultdict(float)
    for number, suffix, weight in evidence:
        observed[number] += weight
        suffixes[suffix] += weight
        for position, digit in enumerate(number):
            votes[position][digit] += weight
    # El desempate se ordena por el propio dígito para que dos corridas del
    # mismo lote den siempre el mismo resultado: con pesos idénticos no hay
    # evidencia que distinga, pero la salida no puede depender del orden en
    # que llegaron las páginas.
    number = "".join(
        max(sorted(slot.items()), key=lambda item: item[1])[0]
        for slot in votes
    )
    if number not in observed:
        number = max(sorted(observed.items()), key=lambda item: item[1])[0]
    suffix = max(sorted(suffixes.items()), key=lambda item: item[1])[0]
    winner = f"HP-{number}{suffix}"
    # ``evidence`` ya eliminó escaneos repetidos del mismo log_number. El
    # número que habilita una inferencia automática tiene que contar páginas
    # físicas independientes, no filas: dos copias del mismo escaneo siguen
    # siendo un solo respaldo.
    matches = [
        weight for observed_number, observed_suffix, weight in evidence
        if observed_number == number and observed_suffix == suffix
    ]
    confidence = (
        round(sum(matches) / len(matches), 3) if matches
        else round(min(observed[number], 1.0), 3)
    )
    return winner, len(matches), confidence


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

    winner_info = _book_winner(entries)
    if winner_info is None:
        return 0, 0
    winner, count, winner_confidence = winner_info

    corrected = 0
    flagged = 0
    for page, field in entries:
        if field.value == winner:
            # Una lectura propia débil no tiene por qué ir a Revisar cuando
            # varias páginas independientes leyeron exactamente lo mismo. El
            # consenso no cambia su valor: únicamente aporta el respaldo que
            # le faltaba y conserva esa procedencia para auditoría.
            if (
                field.status is not Status.OK
                and count >= AUTO_INDEX_MIN_VOTES
                and winner_confidence >= _MIN_BOOK_AUTO_CONFIDENCE
            ):
                field.confidence = max(field.confidence, winner_confidence)
                field.status = Status.OK
                field.source = "book_correction"
                field.inference_method = "book_consensus_confirmation"
                field.votes = count
                field.comment = (
                    f"{field.comment} | Confirmed by book consensus "
                    f"({count} vote(s))"
                ).strip(" |")
                _recompute_page_status(page)
            continue
        original = field.value
        if original and original not in field.alternatives:
            field.alternatives.append(original)
        field.value = winner
        field.confidence = winner_confidence
        # Se conserva la inferencia, pero no se borra la duda que la produjo.
        # Una lectura canónica distinta puede significar que el log_number
        # también se leyó mal y que esta página ni siquiera pertenece al libro
        # cuya matrícula ganó. Ponerla en OK la enviaba bajo el separador de
        # otro avión. Las lecturas vacías o inválidas sí pueden repararse de
        # forma automática cuando el consenso tiene respaldo y confianza.
        canonical_original = bool(
            original and _CANONICAL_MATRICULA_RE.fullmatch(original)
        )
        sufficiently_supported = (
            count >= AUTO_INDEX_MIN_VOTES
            and winner_confidence >= _MIN_BOOK_AUTO_CONFIDENCE
        )
        field.status = (
            Status.OK
            if not canonical_original and sufficiently_supported
            else Status.WARNING
        )
        field.source = "book_correction"
        field.inference_method = "book_digit_consensus"
        # Cuantas paginas del libro leyeron entera esta matricula. La pagina
        # no la leyo —por eso se corrige—, asi que es todo el respaldo que
        # tiene, y con uno solo no alcanza para indexarla sin mirar.
        field.votes = count
        if original:
            field.comment = (
                f"Corrected from {original!r} by book consensus "
                f"({count} vote(s))"
            )
            if canonical_original:
                field.comment += "; conflicting registration requires review"
            flagged += 1
        else:
            field.comment = (
                f"Inferred from book readings: {winner} ({count} vote(s))"
            )
            if not sufficiently_supported:
                field.comment += "; insufficient support for auto index"
            corrected += 1
        _recompute_page_status(page)

    logger.info(
        f"[Libro] Matrícula dominante {winner} ({count} votos, "
        f"conf={winner_confidence}) | corregidas: {corrected} | "
        f"discrepantes sobrescritas: {flagged}"
    )
    return corrected, flagged


def _recompute_page_status(page: PageResult) -> None:
    """Recalcula el estado de una página con la política de indexación.

    La política vive en ``app.validation.page_status`` porque la comparten
    los tres correctores y la validación de la plantilla: si cada uno
    contara los campos a su manera, activar la verificación de matrículas
    cambiaba el estado de páginas que nadie había tocado.
    """
    if page.blank:
        return
    recompute_page_status(page)


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

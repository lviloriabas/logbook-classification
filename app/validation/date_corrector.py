"""Inferencia conservadora de fechas guiada por ``log_number`` y evidencia OCR.

Las paginas de un mismo libro pueden llegar repartidas entre varios PDFs o
en un orden distinto al de la bitacora. Por eso este modulo usa el numero de
bitacora legible solo para establecer la secuencia. El valor que se propaga
debe venir de lecturas directas confiables, no del numero ni de otra
inferencia previa.

La politica de inferencia es deliberadamente asimetrica:

* mes y ano pueden inferirse entre dos anclas directas compatibles;
* en los extremos se permite una extrapolacion corta con dos anclas locales;
* el dia nunca se infiere. Si falta, ``page.date`` permanece vacio.

Una inferencia conserva su procedencia en ``FieldResult`` y queda en WARNING,
nunca se presenta como una lectura OCR directa en estado OK.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import date
from itertools import product
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from loguru import logger

from app.models.schemas import FieldResult, PageResult, Status, ValidationReport
from app.utils.postprocess import MESES, _parse_month, combine_date
from app.validation.book_corrector import _recompute_page_status, _recompute_summary
from app.validation.grouping import group_books, log_number

DATE_FIELD_IDS = ("day", "month", "year")
YEAR_FIELD_ID = "year"
DATE_RE = re.compile(r"^\d{4}/\d{2}/\d{2}$")

# Las anclas directas deben tener al menos la confianza que dispara el
# WARNING normal del OCR. Valores por debajo de esto no deben propagar errores.
MIN_DIRECT_CONFIDENCE = 0.5
# Para inferir un tramo se necesitan dos anclas del mismo componente.
MIN_ANCHORS = 2
# La extrapolacion a un extremo es mas arriesgada que un intervalo cerrado.
MAX_EDGE_LOG_SPAN = 10
# El consenso solo se activa con evidencia suficiente y una mayoría clara.
MIN_YEAR_CONSENSUS_READINGS = 3
MIN_YEAR_CONSENSUS_COUNT = 2
MIN_YEAR_CONSENSUS_RATIO = 0.60

def _field(page: PageResult, field_id: str) -> Optional[FieldResult]:
    for field in page.fields:
        if field.field_id == field_id:
            return field
    return None


def _month_number(value: Optional[str]) -> Optional[int]:
    """Devuelve el mes numerico desde texto o digitos, o ``None``."""
    if not value:
        return None
    raw = re.sub(r"[^\dA-Za-z]", "", value).upper()
    if not raw:
        return None
    if not re.search(r"[A-Za-z]", raw):
        digits = re.sub(r"[^\d]", "", raw)
        if not digits:
            return None
        try:
            month = int(digits)
        except ValueError:
            return None
        return month if 1 <= month <= 12 else None
    return _parse_month(raw)


def _year_normalize(value: Optional[str]) -> Optional[str]:
    """Normaliza un ano valido a dos digitos, sin aceptar anos de 3 digitos."""
    if not value:
        return None
    digits = re.sub(r"[^\d]", "", value)
    if len(digits) == 4:
        year = int(digits)
        if not 2000 <= year <= 2100:
            return None
        return digits[-2:]
    if len(digits) == 2:
        year = 2000 + int(digits)
        return digits if 2000 <= year <= 2100 else None
    return None


def _day_normalize(value: Optional[str]) -> Optional[str]:
    """Normaliza un dia valido; no se usa para inferir valores."""
    if not value:
        return None
    digits = re.sub(r"[^\d]", "", value)
    if not digits:
        return None
    try:
        day = int(digits)
    except ValueError:
        return None
    return f"{day:02d}" if 1 <= day <= 31 else None


def _format_month(month: int) -> str:
    for name, number in MESES.items():
        if number == month:
            return name
    return str(month)


def _format_component(field_id: str, value: str) -> str:
    if field_id == "month":
        return _format_month(int(value))
    if field_id == YEAR_FIELD_ID:
        return f"{int(value):02d}"
    return value


def _component_candidates(
    field: Optional[FieldResult], normalize: Normalizer,
) -> List[str]:
    """Valores canónicos: lectura elegida primero y luego alternativas."""
    if field is None:
        return []
    candidates: List[str] = []
    for raw in [field.value, *field.alternatives]:
        value = normalize(raw)
        if value is not None and value not in candidates:
            candidates.append(value)
    return candidates


def _date_candidates(page: PageResult) -> List[Tuple[date, Tuple[str, str, str]]]:
    """Fechas calendario posibles a partir de la evidencia OCR de la página."""
    days = _component_candidates(_field(page, "day"), _day_normalize)
    months = _component_candidates(_field(page, "month"), _month_number)
    years = _component_candidates(_field(page, YEAR_FIELD_ID), _year_normalize)
    candidates: List[Tuple[date, Tuple[str, str, str]]] = []
    for day_value, month_value, year_value in product(days, months, years):
        try:
            parsed = date(
                2000 + int(year_value), int(month_value), int(day_value)
            )
        except ValueError:
            continue
        candidates.append((parsed, (day_value, month_value, year_value)))
    return candidates


def _resolve_sequence_alternatives(book: Sequence[PageResult]) -> int:
    """Elige alternativas OCR solo si reducen regresiones del mismo libro.

    La búsqueda es dinámica para no explotar combinatoriamente. Cada estado
    conserva el menor costo hasta una fecha candidata. La lectura actual vale
    cero cambios; usar una alternativa cuesta una unidad por componente.
    """
    pages = sorted(
        (page for page in book if log_number(page) is not None),
        key=lambda page: (log_number(page), page.page_number),  # type: ignore[arg-type]
    )
    rows = [(page, _date_candidates(page)) for page in pages]
    rows = [(page, candidates) for page, candidates in rows if candidates]
    if len(rows) < 2:
        return 0

    def local_changes(page: PageResult, values: Tuple[str, str, str]) -> int:
        day = _field(page, "day")
        month = _field(page, "month")
        year = _field(page, YEAR_FIELD_ID)
        current = (
            _day_normalize(day.value if day else None),
            _month_number(month.value if month else None),
            _year_normalize(year.value if year else None),
        )
        normalized = (values[0], int(values[1]), values[2])
        return sum(left != right for left, right in zip(current, normalized))

    # estado: candidato actual -> (regresiones, cambios, saltos, camino)
    first_page, first_candidates = rows[0]
    states = {
        candidate[0]: (
            0, local_changes(first_page, candidate[1]), 0, [candidate]
        )
        for candidate in first_candidates
    }
    for page, candidates in rows[1:]:
        next_states = {}
        for candidate in candidates:
            parsed, values = candidate
            best = None
            for previous_date, state in states.items():
                regressions, changes, jumps, path = state
                score = (
                    regressions + int(parsed < previous_date),
                    changes + local_changes(page, values),
                    jumps + abs((parsed - previous_date).days),
                    [*path, candidate],
                )
                if best is None or score[:3] < best[:3]:
                    best = score
            existing = next_states.get(parsed)
            if best is not None and (
                existing is None or best[:3] < existing[:3]
            ):
                next_states[parsed] = best
        states = next_states
    if not states:
        return 0
    best = min(states.values(), key=lambda item: item[:3])

    current_dates = [candidates[0] for _page, candidates in rows]
    current_regressions = sum(
        right[0] < left[0]
        for left, right in zip(current_dates, current_dates[1:])
    )
    if best[0] >= current_regressions:
        return 0

    corrected = 0
    for (page, _candidates), (_parsed, values) in zip(rows, best[3]):
        for field_id, normalized, formatter in (
            ("day", values[0], lambda value: str(int(value))),
            ("month", values[1], _format_component),
            (YEAR_FIELD_ID, values[2], lambda value: f"{int(value):02d}"),
        ):
            field = _field(page, field_id)
            if field is None:
                continue
            formatted = (
                _format_component("month", normalized)
                if field_id == "month" else formatter(normalized)
            )
            current = (
                _day_normalize(field.value) if field_id == "day" else
                _month_number(field.value) if field_id == "month" else
                _year_normalize(field.value)
            )
            expected = (
                str(int(normalized)) if field_id == "month" else normalized
            )
            if str(current) == expected:
                continue
            previous = field.value
            if previous and previous not in field.alternatives:
                field.alternatives.append(previous)
            field.value = formatted
            field.status = Status.WARNING
            field.source = "book_correction"
            field.inference_method = "log_number_sequence_candidate"
            field.comment = (
                f"OCR alternative selected by nondecreasing book sequence: "
                f"{previous!r} -> {formatted!r}"
            )
            corrected += 1
        _recombine(page)
    return corrected


def _correct_year_by_book_consensus(book: Sequence[PageResult]) -> int:
    """Corrige años OCR aislados usando mayoría y posición en el libro.

    Un año no adyacente al mayoritario (p. ej. 21/24 frente a 26) no puede
    ser una transición anual real y se corrige. Un año adyacente se conserva
    únicamente cuando forma el prefijo o sufijo cronológico esperado, para
    permitir libros que cruzan de diciembre a enero.
    """
    pages = sorted(
        (page for page in book if log_number(page) is not None and not page.blank),
        key=lambda page: (log_number(page), page.page_number),  # type: ignore[arg-type]
    )
    observed: List[Tuple[int, PageResult, FieldResult, str]] = []
    for index, page in enumerate(pages):
        field = _field(page, YEAR_FIELD_ID)
        value = _year_normalize(field.value if field else None)
        if field is not None and value is not None:
            observed.append((index, page, field, value))
    if len(observed) < MIN_YEAR_CONSENSUS_READINGS:
        return 0

    counts = Counter(value for _index, _page, _field_result, value in observed)
    ranked = counts.most_common()
    majority_year, majority_count = ranked[0]
    runner_count = ranked[1][1] if len(ranked) > 1 else 0
    ratio = majority_count / len(observed)
    if (
        majority_count < MIN_YEAR_CONSENSUS_COUNT
        or majority_count <= runner_count
        or ratio < MIN_YEAR_CONSENSUS_RATIO
    ):
        return 0

    majority_positions = [
        index for index, _page, _field_result, value in observed
        if value == majority_year
    ]
    first_majority = min(majority_positions)
    last_majority = max(majority_positions)
    majority_number = int(majority_year)
    corrected = 0
    for index, page, field, value in observed:
        if value == majority_year:
            continue
        delta = int(value) - majority_number
        plausible_previous_prefix = delta == -1 and index < first_majority
        plausible_next_suffix = delta == 1 and index > last_majority
        if plausible_previous_prefix or plausible_next_suffix:
            continue

        previous = field.value
        if previous and previous not in field.alternatives:
            field.alternatives.append(previous)
        field.value = majority_year
        field.confidence = round(min(0.95, 0.55 + ratio * 0.40), 3)
        field.status = Status.WARNING
        field.source = "book_correction"
        field.inference_method = "log_number_year_consensus"
        field.comment = (
            f"Year corrected by book majority ({majority_count}/"
            f"{len(observed)}): {previous!r} -> {majority_year!r}"
        )
        _recombine(page)
        corrected += 1
    return corrected


def _is_direct_anchor(field: Optional[FieldResult], value: Optional[str]) -> bool:
    """Indica si una lectura directa confiable puede ser una ancla.

    ``log_number`` no aporta el valor de la fecha. Solo las lecturas en OK
    pueden aportar evidencia; warnings, errores y valores ya inferidos se
    conservan para auditoria, pero no propagan informacion.
    """
    comment = (field.comment or "").lower() if field is not None else ""
    alternatives = field.alternatives if field is not None else []
    unreliable_note = any(token in comment for token in (
        "fuzzy", "numeric handwritten month", "low confidence",
        "conflicts with", "regression",
    ))
    return bool(
        field is not None
        and value is not None
        and field.source not in {"inferred", "book_correction"}
        and field.status is Status.OK
        and field.confidence >= MIN_DIRECT_CONFIDENCE
        and not alternatives
        and not unreliable_note
    )


Anchor = Tuple[int, str, PageResult]
Normalizer = Callable[[Optional[str]], Optional[str]]


def _anchors(
    book: Sequence[PageResult], field_id: str, normalize: Normalizer
) -> List[Anchor]:
    """Obtiene lecturas confiables y las ordena por ``log_number``.

    El numero solo ordena las evidencias. El valor de cada ancla siempre sale
    del campo de fecha que fue leido y validado directamente.
    """
    anchors: List[Anchor] = []
    for page in book:
        number = log_number(page)
        field = _field(page, field_id)
        value = normalize(field.value if field else None)
        if (
            number is None
            or page.alignment_quality != "ok"
            or not _is_direct_anchor(field, value)
        ):
            continue
        anchors.append((number, value, page))  # type: ignore[arg-type]
    return sorted(anchors, key=lambda item: (item[0], item[2].page_number))


def _inferred_confidence(anchor_count: int, span: int = 0) -> float:
    """Confianza acotada para una inferencia, siempre menor que una lectura."""
    confidence = 0.64 + min(anchor_count, 5) * 0.05
    if span > 0:
        confidence -= min(0.10, span * 0.005)
    return round(min(0.90, max(0.60, confidence)), 3)


def _append_comment(field: FieldResult, comment: str) -> None:
    field.comment = f"{field.comment} | {comment}" if field.comment else comment


def _set_inferred_component(
    page: PageResult,
    field_id: str,
    value: str,
    method: str,
    anchor_numbers: Sequence[int],
) -> bool:
    """Escribe mes o ano inferido y deja su procedencia estructurada."""
    field = _field(page, field_id)
    if field is None:
        return False
    formatted = _format_component(field_id, value)
    field.value = formatted
    field.status = Status.WARNING
    field.confidence = _inferred_confidence(
        len(anchor_numbers),
        max(anchor_numbers) - min(anchor_numbers)
        if len(anchor_numbers) > 1 else 0,
    )
    field.source = "inferred"
    field.inference_method = method
    field.comment = (
        f"Inferred {field_id} from log_number anchors "
        f"{', '.join(str(number) for number in anchor_numbers)}: {formatted}"
    )
    return True


def _mark_interval_conflict(
    page: PageResult, field_id: str, expected: str, anchor_numbers: Sequence[int]
) -> bool:
    """Marca una lectura directa que contradice un intervalo compatible."""
    field = _field(page, field_id)
    if field is None or not field.value:
        return False
    field.status = Status.WARNING
    field.inference_method = "log_number_interval_conflict"
    _append_comment(
        field,
        f"Conflicts with {field_id} interval {expected} "
        f"(log_number anchors {', '.join(str(n) for n in anchor_numbers)})",
    )
    return True


def _infer_between_anchors(
    book: Sequence[PageResult],
    field_id: str,
    normalize: Normalizer,
) -> Tuple[int, int]:
    """Infiere valores solo dentro de un intervalo con dos anclas iguales."""
    anchors = _anchors(book, field_id, normalize)
    if len(anchors) < MIN_ANCHORS:
        return 0, 0

    filled = 0
    flagged = 0
    known_pages = sorted(
        (page for page in book if log_number(page) is not None),
        key=lambda page: (log_number(page), page.page_number),  # type: ignore[arg-type]
    )
    for page in known_pages:
        number = log_number(page)
        field = _field(page, field_id)
        if number is None or field is None or page.alignment_quality != "ok":
            continue
        before = [anchor for anchor in anchors if anchor[0] < number]
        after = [anchor for anchor in anchors if anchor[0] > number]
        if not before or not after:
            continue
        left = before[-1]
        right = after[0]
        if left[1] != right[1]:
            continue

        interior = [
            anchor for anchor in anchors
            if left[0] < anchor[0] < right[0]
            and not (anchor[0] == number and anchor[2] is page)
        ]
        if any(anchor[1] != left[1] for anchor in interior):
            continue

        anchor_numbers = [left[0], *[anchor[0] for anchor in interior], right[0]]
        current = normalize(field.value)
        if current == left[1] and field.status is not Status.ERROR:
            continue
        if current is not None and field.status is not Status.ERROR:
            flagged += int(_mark_interval_conflict(
                page, field_id, _format_component(field_id, left[1]),
                anchor_numbers,
            ))
            continue
        filled += int(_set_inferred_component(
            page, field_id, left[1], "log_number_interval", anchor_numbers
        ))
    return filled, flagged


def _infer_edges(
    book: Sequence[PageResult],
    field_id: str,
    normalize: Normalizer,
) -> int:
    """Infiere un tramo corto al inicio o final con dos anclas iguales."""
    anchors = _anchors(book, field_id, normalize)
    if len(anchors) < MIN_ANCHORS:
        return 0
    pages = sorted(
        (page for page in book if log_number(page) is not None),
        key=lambda page: (log_number(page), page.page_number),  # type: ignore[arg-type]
    )
    filled = 0

    edge_specs: List[Tuple[List[Anchor], List[PageResult]]] = []
    first_two = anchors[:2]
    last_two = anchors[-2:]
    if first_two[0][1] == first_two[1][1]:
        edge_specs.append((first_two, [
            page for page in pages
            if log_number(page) < first_two[0][0]  # type: ignore[operator]
        ]))
    if last_two[0][1] == last_two[1][1]:
        edge_specs.append((last_two, [
            page for page in pages
            if log_number(page) > last_two[-1][0]  # type: ignore[operator]
        ]))

    for edge_anchors, targets in edge_specs:
        if not edge_anchors:
            continue
        value = edge_anchors[0][1]
        for page in targets:
            number = log_number(page)
            field = _field(page, field_id)
            if number is None or field is None \
                    or page.alignment_quality != "ok":
                continue
            distance = (
                edge_anchors[0][0] - number
                if number < edge_anchors[0][0]
                else number - edge_anchors[-1][0]
            )
            if distance > MAX_EDGE_LOG_SPAN:
                continue
            current = normalize(field.value)
            if current is not None and field.status is not Status.ERROR:
                continue
            filled += int(_set_inferred_component(
                page, field_id, value, "log_number_local_consensus",
                [anchor[0] for anchor in edge_anchors],
            ))
    return filled


def _recombine(page: PageResult) -> None:
    """Combina la fecha solo si dia, mes y ano son validos."""
    day = _field(page, "day")
    month = _field(page, "month")
    year = _field(page, YEAR_FIELD_ID)
    if not (day and month and year):
        page.date = None
        return
    if any(part.status is Status.ERROR for part in (day, month, year)):
        page.date = None
        return

    normalized_day = _day_normalize(day.value)
    normalized_month = _month_number(month.value)
    normalized_year = _year_normalize(year.value)
    if normalized_day is None or normalized_month is None \
            or normalized_year is None:
        page.date = None
        return

    combined, _note = combine_date(
        normalized_day,
        _format_month(normalized_month),
        normalized_year,
    )
    page.date = combined if DATE_RE.fullmatch(combined or "") else None


def _check_regressions(book: Sequence[PageResult]) -> int:
    """Marca regresiones usando exclusivamente el orden de ``log_number``."""
    previous: Optional[Tuple[int, int, int]] = None
    regressions = 0
    pages = sorted(
        (page for page in book if log_number(page) is not None),
        key=lambda page: (log_number(page), page.page_number),  # type: ignore[arg-type]
    )
    for page in pages:
        if not page.date or not DATE_RE.fullmatch(page.date):
            continue
        current = tuple(int(part) for part in page.date.split("/"))
        if previous is not None and current < previous:
            year = _field(page, YEAR_FIELD_ID)
            if year is not None and year.status is not Status.ERROR:
                year.status = Status.WARNING
                year.inference_method = "log_number_regression"
                _append_comment(year, "Date regression by log_number")
                regressions += 1
        previous = current
    return regressions


def _flag_unresolved(book: Sequence[PageResult]) -> int:
    """Marca faltantes sin convertir el dia ausente en una inferencia."""
    unresolved = 0
    for page in book:
        if page.blank:
            continue
        _recombine(page)
        if page.date is not None:
            continue
        unresolved += 1

        day = _field(page, "day")
        month = _field(page, "month")
        year = _field(page, YEAR_FIELD_ID)
        if day is not None and _day_normalize(day.value) is None:
            day.status = Status.WARNING
            day.inference_method = "date_incomplete"
            day.comment = "Day unresolved; date left incomplete"
        if month is not None and _month_number(month.value) is None:
            month.status = Status.ERROR
            month.inference_method = "date_unresolved"
            month.comment = "Month unresolved after log_number inference"
        if year is not None and _year_normalize(year.value) is None:
            year.status = Status.ERROR
            year.inference_method = "date_unresolved"
            year.comment = "Year unresolved after log_number inference"
        if (
            day is not None
            and month is not None
            and year is not None
            and _day_normalize(day.value) is not None
            and _month_number(month.value) is not None
            and _year_normalize(year.value) is not None
        ):
            year.status = Status.ERROR
            year.inference_method = "invalid_calendar_date"
            year.comment = "Date is not a valid calendar date"
    return unresolved


def correct_dates_by_book(
    reports: List[ValidationReport],
) -> Dict[str, int]:
    """Completa mes y ano por intervalos de ``log_number``.

    El resultado ``corrected`` cuenta componentes inferidos o corregidos,
    no paginas.
    ``days_filled`` se conserva en cero como garantia explicita de que este
    corrector nunca inventa el dia.
    """
    books = group_books(reports)
    stats: Dict[str, int] = {
        "books": len(books),
        "corrected": 0,
        "flagged": 0,
        "regressions": 0,
        "sequence_candidates": 0,
        "years_consensus": 0,
        "months_filled": 0,
        "years_filled": 0,
        "days_filled": 0,
        "unresolved": 0,
    }

    for book in books:
        for page in book:
            _recombine(page)

        year_consensus = _correct_year_by_book_consensus(book)
        stats["years_consensus"] += year_consensus
        sequence_candidates = _resolve_sequence_alternatives(book)
        stats["sequence_candidates"] += sequence_candidates

        years, year_flags = _infer_between_anchors(
            book, YEAR_FIELD_ID, _year_normalize
        )
        years += _infer_edges(book, YEAR_FIELD_ID, _year_normalize)
        months, month_flags = _infer_between_anchors(
            book, "month", _month_number
        )
        months += _infer_edges(book, "month", _month_number)

        for page in book:
            _recombine(page)
        regressions = _check_regressions(book)
        unresolved = _flag_unresolved(book)

        stats["years_filled"] += years
        stats["months_filled"] += months
        stats["corrected"] += (
            years + months + year_consensus + sequence_candidates
        )
        stats["flagged"] += year_flags + month_flags
        stats["regressions"] += regressions
        stats["unresolved"] += unresolved
        for page in book:
            _recompute_page_status(page)

    for report in reports:
        _recompute_summary(report)
    logger.info(f"Corrector de fechas por log_number: {stats}")
    return stats

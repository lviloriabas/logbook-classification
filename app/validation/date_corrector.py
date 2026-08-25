"""Inferencia conservadora de fechas guiada por ``log_number`` y evidencia OCR.

Las paginas de un mismo libro pueden llegar repartidas entre varios PDFs o
en un orden distinto al de la bitacora. Por eso este modulo usa el numero de
bitacora legible solo para establecer la secuencia. El valor que se propaga
debe venir de lecturas directas confiables, no del numero ni de otra
inferencia previa.

La politica de inferencia es deliberadamente asimetrica:

* mes y ano pueden inferirse entre dos anclas directas compatibles;
* en los extremos se permite una extrapolacion corta con dos anclas locales;
* una lectura mensual posicional clara puede actuar como ancla aun si su
  confianza aislada es baja;
* el día leído no se sustituye nunca; el día que no se leyó se completa con
  el último que cabe en la secuencia del libro (como mucho, el último del
  mes), porque una página sin día es una bitácora entera por indexar a mano
  aunque todo lo demás se haya leído. La política del CSV (día específico o
  fin de mes) sigue decidiendo cómo se representa la fecha.

Una inferencia conserva su procedencia en ``FieldResult`` y queda en WARNING,
nunca se presenta como una lectura OCR directa en estado OK.
"""

from __future__ import annotations

import json
import re
from calendar import monthrange
from collections import Counter
from datetime import date
from itertools import product
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from loguru import logger

from app.models.schemas import FieldResult, PageResult, Status, ValidationReport
from app.utils.postprocess import MESES, _parse_month, combine_date
from app.validation.book_corrector import (
    _BOOK_STORAGE_KEY_RE,
    _recompute_page_status,
    _recompute_summary,
    _storage_key,
)
from app.validation.grouping import group_books, log_number
from app.validation.page_status import AUTO_INDEX_MIN_VOTES

DATE_FIELD_IDS = ("day", "month", "year")
YEAR_FIELD_ID = "year"
DATE_RE = re.compile(r"^\d{4}/\d{2}/\d{2}$")

# Las anclas directas deben tener al menos la confianza que dispara el
# WARNING normal del OCR. Valores por debajo de esto no deben propagar errores.
MIN_DIRECT_CONFIDENCE = 0.5
# Una lectura exacta por ranuras conserva evidencia física suficiente para el
# mes aunque la confianza del carácter manuscrito quede en WARNING.
MIN_POSITIONAL_MONTH_CONFIDENCE = 0.35
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


def _is_positional_month_anchor(
    field: Optional[FieldResult], value: Optional[str]
) -> bool:
    """Acepta un mes canónico reconstruido directamente desde sus casillas."""
    comment = (field.comment or "").lower() if field is not None else ""
    return bool(
        field is not None
        and value is not None
        and field.status is Status.WARNING
        and field.confidence >= MIN_POSITIONAL_MONTH_CONFIDENCE
        and field.inference_method in {"ranuras", "date_cells"}
        and field.source not in {"inferred", "book_correction"}
        and not field.alternatives
        and "fuzzy" not in comment
        and "numeric handwritten month" not in comment
        and "conflict" not in comment
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
            or not (
                _is_direct_anchor(field, value)
                or (
                    field_id == "month"
                    and _is_positional_month_anchor(field, value)
                )
            )
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
    previous = field.value
    if previous and previous != formatted and previous not in field.alternatives:
        field.alternatives.append(previous)
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


def _fill_from_book_consensus(
    book: Sequence[PageResult],
    field_id: str,
    normalize: Normalizer,
    anchors: Sequence[Anchor],
) -> int:
    """Completa un componente cuando todo el libro coincide en su valor.

    Un libro es un solo avión llenado de corrido. Si todas las lecturas
    confiables del libro dicen el mismo mes (o el mismo año), no queda otra
    opción posible para las páginas que no se dejaron leer: no hay tramo que
    interpolar ni extremo que extrapolar, hay un único valor. Antes esas
    páginas se quedaban sin fecha porque la inferencia pedía una ancla a cada
    lado, y una página sin fecha es una bitácora que hay que indexar a mano.

    ``anchors`` se calcula **antes** de interpolar: la interpolación marca
    como "en conflicto" las lecturas que contradicen un intervalo, y esas
    lecturas dejan de ser anclas. Recalcularlas aquí haría unánime un libro
    que no lo era y llenaría el hueco con el mes equivocado.

    La página queda en WARNING y con la procedencia escrita, igual que
    cualquier otra inferencia: nunca se presenta como lectura directa.
    """
    if len(anchors) < MIN_ANCHORS:
        return 0
    values = {anchor[1] for anchor in anchors}
    if len(values) != 1:
        return 0
    value = values.pop()
    numbers = [anchor[0] for anchor in anchors]
    filled = 0
    for page in book:
        if page.blank:
            continue
        field = _field(page, field_id)
        if field is None or normalize(field.value) is not None:
            continue
        filled += int(_set_inferred_component(
            page, field_id, value, "book_consensus", numbers
        ))
    return filled


def _resolved_date(page: PageResult) -> Optional[Tuple[int, int, int]]:
    """(año, mes, día) de la página cuando los tres están resueltos."""
    day = _day_normalize(_field(page, "day").value if _field(page, "day") else None)
    month = _month_number(
        _field(page, "month").value if _field(page, "month") else None
    )
    year = _year_normalize(
        _field(page, YEAR_FIELD_ID).value
        if _field(page, YEAR_FIELD_ID) else None
    )
    if day is None or month is None or year is None:
        return None
    return (2000 + int(year), month, int(day))


def _neighbour_day(
    dates: Sequence[Optional[Tuple[int, int, int]]],
    index: int,
    step: int,
    month: Tuple[int, int],
) -> Optional[int]:
    """Día de la página resuelta más cercana que cae en el mismo mes."""
    position = index + step
    while 0 <= position < len(dates):
        neighbour = dates[position]
        if neighbour is not None:
            return neighbour[2] if neighbour[:2] == month else None
        position += step
    return None


def _fill_days_to_month_end(book: Sequence[PageResult]) -> int:
    """Completa el día ilegible con el último que cabe en la secuencia.

    Una página con mes y año resueltos pero sin día se quedaba sin fecha, y
    con ella la bitácora entera había que indexarla a mano aunque todo lo
    demás se hubiera leído. Como el libro se llena de corrido, ese día no
    puede ser anterior al de la página previa ni posterior al de la
    siguiente: se escribe el último día que cabe en ese hueco y, si no hay
    página posterior del mismo mes, el último del mes.

    El día queda en WARNING, con su procedencia y su comentario, así que en
    el CSV se distingue de un día leído de la casilla.
    """
    ordered = sorted(
        (page for page in book if not page.blank),
        key=lambda page: (
            log_number(page) if log_number(page) is not None else 1 << 30,
            page.page_number,
        ),
    )
    dates = [_resolved_date(page) for page in ordered]
    filled = 0
    for index, page in enumerate(ordered):
        field = _field(page, "day")
        if field is None or _day_normalize(field.value) is not None:
            continue
        month = _month_number(
            _field(page, "month").value if _field(page, "month") else None
        )
        year = _year_normalize(
            _field(page, YEAR_FIELD_ID).value
            if _field(page, YEAR_FIELD_ID) else None
        )
        if month is None or year is None:
            continue
        full_year = 2000 + int(year)
        last_day = monthrange(full_year, month)[1]
        same_month = (full_year, month)
        after = _neighbour_day(dates, index, 1, same_month)
        before = _neighbour_day(dates, index, -1, same_month)
        day = min(last_day, after if after is not None else last_day)
        if before is not None:
            day = max(day, before)
        previous = field.value
        if previous and previous not in field.alternatives:
            field.alternatives.append(previous)
        field.value = f"{day:02d}"
        field.status = Status.WARNING
        field.confidence = _inferred_confidence(1)
        field.source = "inferred"
        field.inference_method = "month_end_fallback"
        field.comment = (
            f"Day not read; last day that fits the book sequence: {day:02d}"
        )
        dates[index] = (full_year, month, day)
        filled += 1
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
    """Marca los componentes que siguen sin resolverse tras la inferencia."""
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
            month.status = Status.WARNING
            month.inference_method = "date_unresolved"
            month.comment = "Month unresolved after log_number inference"
        if year is not None and _year_normalize(year.value) is None:
            year.status = Status.WARNING
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
            year.status = Status.WARNING
            year.inference_method = "invalid_calendar_date"
            year.comment = "Date is not a valid calendar date"
    return unresolved


# ── Registro de fechas por libro, persistente entre ejecuciones ─────────
#
# Un libro puede llegar repartido en varias ejecuciones: las primeras
# páginas hoy y el resto la semana que viene. La inferencia de arriba solo
# ve las páginas de la ejecución actual, así que un libro cuyas fechas
# legibles se quedaron en la entrega anterior vuelve a empezar sin anclas.
# El registro guarda de cada libro la primera y la última fecha confirmadas
# por lectura directa, y nada más: dos pares "logpage: fecha" por libro
# ocupan unas decenas de bytes, de modo que el archivo sigue siendo del
# tamaño de una lista y no de un historial.
#
# Con las dos anclas basta para resolver el tramo que hay entre ellas: la
# fecha no retrocede dentro del libro, así que toda página intermedia cae
# entre las dos. Si ambas comparten mes (o al menos año), no queda otro
# valor posible para las páginas intermedias que no se dejaron leer. Fuera
# de ese tramo no se infiere nada: una página posterior a la última ancla
# solo tiene garantizado que su fecha no es anterior, y eso no fija el mes.
#
# El registro se pone al día en los dos sentidos. Crece cuando aparecen
# páginas nuevas del mismo libro, y se rehace cuando lo que se lee lo
# contradice con respaldo suficiente: una entrada equivocada que nadie
# corrige envenena la inferencia de todas las ejecuciones siguientes.
BOOK_DATES_FILENAME = "book_fechas.json"

# Una entrada del registro es "logpage": "AAAA-MM-DD".
_REGISTRY_LOGPAGE_RE = re.compile(r"^\d{2}$")
_REGISTRY_DATE_RE = re.compile(r"^(20\d{2})-(\d{2})-(\d{2})$")
# Un libro nunca aporta más de dos anclas al registro: sus extremos.
_MAX_REGISTRY_ANCHORS = 2
# Lecturas directas que debe traer una ejecución para corregir una entrada
# que la contradice. Es el respaldo de dos páginas independientes que ya
# exige el registro de matrículas para dar por buena una lectura.
MIN_REGISTRY_OVERRIDE_READINGS = AUTO_INDEX_MIN_VOTES

RegistryAnchor = Tuple[int, date]


def _registry_anchor(
    key: str, logpage_text: str, iso: object
) -> Optional[RegistryAnchor]:
    """Convierte una entrada del archivo en ancla, o la descarta."""
    if not _REGISTRY_LOGPAGE_RE.fullmatch(logpage_text):
        return None
    logpage = int(logpage_text)
    if ("A" if logpage < 50 else "B") != key[5]:
        return None
    matched = _REGISTRY_DATE_RE.fullmatch(iso) if isinstance(iso, str) else None
    if matched is None:
        return None
    try:
        parsed = date(
            int(matched.group(1)), int(matched.group(2)),
            int(matched.group(3)),
        )
    except ValueError:
        return None
    return (logpage, parsed)


def _load_book_dates(path: Path) -> Dict[str, List[RegistryAnchor]]:
    """Lee el registro; un archivo dañado no detiene el procesamiento."""
    path = Path(path)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning(f"No se pudo leer el registro de fechas {path}: {exc}")
        return {}
    if not isinstance(data, dict):
        logger.warning(f"Registro de fechas inválido: {path}")
        return {}
    stored: Dict[str, List[RegistryAnchor]] = {}
    for key, value in data.items():
        if not isinstance(key, str) or not _BOOK_STORAGE_KEY_RE.fullmatch(key):
            continue
        if (
            not isinstance(value, dict)
            or not 1 <= len(value) <= _MAX_REGISTRY_ANCHORS
        ):
            continue
        anchors: List[RegistryAnchor] = []
        for logpage_text, iso in value.items():
            anchor = (
                _registry_anchor(key, logpage_text, iso)
                if isinstance(logpage_text, str) else None
            )
            if anchor is None:
                anchors = []
                break
            anchors.append(anchor)
        if not anchors:
            continue
        anchors.sort()
        if len(anchors) == _MAX_REGISTRY_ANCHORS and anchors[0][1] > anchors[1][1]:
            # La fecha no retrocede dentro de un libro: el par no es de fiar.
            logger.warning(f"Registro de fechas: se ignora {key}, retrocede")
            continue
        stored[key] = anchors
    return stored


def _save_book_dates(
    path: Path, stored: Dict[str, List[RegistryAnchor]]
) -> None:
    """Escribe solo clave y extremos en JSON compacto y atómico."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {
            key: {
                f"{logpage:02d}": value.isoformat()
                for logpage, value in sorted(anchors)
            }
            for key, anchors in sorted(stored.items())
        },
        ensure_ascii=True,
        separators=(",", ":"),
    )
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(f"{payload}\n", encoding="utf-8")
    temporary.replace(path)


def _confirmed_date(page: PageResult) -> Optional[date]:
    """Fecha de la página cuando sus tres componentes son lectura directa.

    Al registro solo entra lo que se leyó y se validó en la página. Una
    fecha inferida no puede guardarse como ancla: sería la propia
    inferencia dándose la razón en la ejecución siguiente.
    """
    if page.blank or page.alignment_quality != "ok":
        return None
    day_field = _field(page, "day")
    month_field = _field(page, "month")
    year_field = _field(page, YEAR_FIELD_ID)
    day = _day_normalize(day_field.value if day_field else None)
    month = _month_number(month_field.value if month_field else None)
    year = _year_normalize(year_field.value if year_field else None)
    if not (
        _is_direct_anchor(day_field, day)
        and _is_direct_anchor(
            month_field, f"{month:02d}" if month is not None else None
        )
        and _is_direct_anchor(year_field, year)
    ):
        return None
    try:
        return date(2000 + int(year), int(month), int(day))  # type: ignore[arg-type]
    except ValueError:
        return None


def _registry_numbers(key: str, anchors: Sequence[RegistryAnchor]) -> List[int]:
    """log_number completo de cada ancla guardada."""
    return [int(f"{key[:5]}{logpage:02d}") for logpage, _value in anchors]


def _fill_component_from_registry(
    page: PageResult,
    field_id: str,
    value: str,
    key: str,
    anchors: Sequence[RegistryAnchor],
) -> bool:
    """Escribe el componente que el registro deja sin alternativa."""
    field = _field(page, field_id)
    if field is None:
        return False
    if not _set_inferred_component(
        page, field_id, value, "book_dates_registry",
        _registry_numbers(key, anchors),
    ):
        return False
    span = " a ".join(anchor[1].isoformat() for anchor in anchors)
    _append_comment(field, f"Registro del libro {key}: {span}")
    return True


def _fill_from_registry(
    book: Sequence[PageResult], key: str, anchors: Sequence[RegistryAnchor]
) -> int:
    """Completa mes y año del tramo que las anclas guardadas encierran."""
    if len(anchors) < _MAX_REGISTRY_ANCHORS:
        return 0
    (first_page, first_date), (last_page, last_date) = anchors[0], anchors[-1]
    in_span = [
        page for page in book
        if (number := log_number(page)) is not None
        and first_page <= number % 100 <= last_page
    ]
    for page in in_span:
        confirmed = _confirmed_date(page)
        if confirmed is not None and not first_date <= confirmed <= last_date:
            # La ejecución actual contradice lo guardado. Ni se corrige el
            # registro por iniciativa propia ni se usa para inferir: el
            # conflicto lo resuelve quien revise.
            logger.warning(
                f"Registro del libro {key} ({first_date.isoformat()} a "
                f"{last_date.isoformat()}) contra la lectura "
                f"{confirmed.isoformat()}: no se usa en esta ejecución"
            )
            return 0
    same_year = first_date.year == last_date.year
    same_month = same_year and first_date.month == last_date.month
    if not same_year:
        return 0
    filled = 0
    for page in in_span:
        if page.blank:
            continue
        year_field = _field(page, YEAR_FIELD_ID)
        if year_field is not None and _year_normalize(year_field.value) is None:
            filled += int(_fill_component_from_registry(
                page, YEAR_FIELD_ID, f"{first_date.year % 100:02d}",
                key, anchors,
            ))
        month_field = _field(page, "month")
        if (
            same_month
            and month_field is not None
            and _month_number(month_field.value) is None
        ):
            filled += int(_fill_component_from_registry(
                page, "month", str(first_date.month), key, anchors,
            ))
    return filled


def _extremes(anchors: Sequence[RegistryAnchor]) -> List[RegistryAnchor]:
    """Primera y última ancla; una sola se guarda tal cual."""
    ordered = sorted(anchors)
    return [ordered[0], ordered[-1]] if len(ordered) > 1 else [ordered[0]]


def _describe_anchors(anchors: Sequence[RegistryAnchor]) -> str:
    """Anclas en una línea, para el log de una corrección."""
    return ", ".join(
        f"{logpage:02d}={value.isoformat()}"
        for logpage, value in sorted(anchors)
    ) or "nada"


def _merge_registry_anchors(
    key: str,
    previous: Sequence[RegistryAnchor],
    observed: Sequence[RegistryAnchor],
) -> Tuple[Optional[List[RegistryAnchor]], str]:
    """Concilia lo guardado con lo que se acaba de leer del libro.

    Devuelve las anclas que quedan guardadas y qué se hizo con ellas:
    ``"nuevo"``, ``"ampliado"``, ``"corregido"``, o ``""`` cuando nada
    cambia.

    Mientras lo guardado y lo leído se sostengan a la vez, la entrada solo
    crece hacia los extremos. Si se contradicen (la misma página con otra
    fecha, o una fecha que obligaría al libro a retroceder) uno de los dos
    describe un libro que no existe, y dejarlo estar significa seguir
    infiriendo desde un dato falso en todas las ejecuciones que vengan. Gana
    entonces la ejecución actual, siempre que traiga por lo menos
    ``MIN_REGISTRY_OVERRIDE_READINGS`` lecturas directas del libro
    coherentes entre sí: son páginas que se acaban de leer y de validar,
    mientras que lo guardado pudo salir de una lectura equivocada que nadie
    volvió a mirar. La entrada se rehace con esas lecturas y el cambio queda
    en el log.

    Con una sola lectura en contra no se toca nada: un OCR suelto no basta
    para borrar lo que ya estaba, y el conflicto queda para revisión.
    """
    merged: Dict[int, date] = dict(previous)
    conflicts: List[str] = []
    for logpage, value in observed:
        known = merged.get(logpage)
        if known is not None and known != value:
            conflicts.append(
                f"la página {logpage:02d} pasa de {known.isoformat()} a "
                f"{value.isoformat()}"
            )
        merged[logpage] = value
    ordered = sorted(merged.items())
    if any(
        later < earlier
        for (_a, earlier), (_b, later) in zip(ordered, ordered[1:])
    ):
        conflicts.append("la fecha del libro retrocedería")
    if not conflicts:
        anchors = _extremes(ordered)
        if list(previous) == anchors:
            return anchors, ""
        return anchors, "ampliado" if previous else "nuevo"
    if len(observed) < MIN_REGISTRY_OVERRIDE_READINGS:
        logger.warning(
            f"Registro del libro {key}: {'; '.join(conflicts)}. Una lectura "
            f"suelta no reemplaza lo guardado; queda para revisión"
        )
        return None, ""
    corrected = _extremes(observed)
    logger.warning(
        f"Registro del libro {key}: {'; '.join(conflicts)}. Mandan las "
        f"{len(observed)} lecturas directas de esta ejecución: "
        f"{_describe_anchors(previous)} pasa a "
        f"{_describe_anchors(corrected)}"
    )
    return corrected, "corregido"


def learn_book_dates(reports: List[ValidationReport], path: Path) -> int:
    """Pone al día los extremos de fecha confirmados de cada libro.

    Solo aprende de lecturas directas en OK, con la página bien alineada y
    el ``log_number`` legible. El archivo guarda por libro un par de
    entradas como ``"23159B":{"52":"2025-05-14","97":"2025-06-02"}``: ni
    páginas, ni imágenes, ni historial de ejecuciones.

    Una entrada no solo crece: si lo que se acaba de leer la contradice y
    hay respaldo suficiente, se rehace con las lecturas nuevas. Las entradas
    imposibles o dañadas del archivo se descartan al leerlo y no vuelven a
    escribirse.

    Returns:
        Cuántos libros dejaron una entrada nueva, ampliada o corregida.
    """
    path = Path(path)
    stored = _load_book_dates(path)
    counters = {"nuevo": 0, "ampliado": 0, "corregido": 0}
    for book in group_books(reports):
        key = _storage_key(book)
        if key is None:
            continue
        observed: List[RegistryAnchor] = []
        for page in book:
            number = log_number(page)
            confirmed = _confirmed_date(page) if number is not None else None
            if number is None or confirmed is None:
                continue
            observed.append((number % 100, confirmed))
        if not observed:
            continue
        observed.sort()
        if any(
            later < earlier
            for (_a, earlier), (_b, later) in zip(observed, observed[1:])
        ):
            logger.warning(
                f"Libro {key}: las fechas leídas retroceden, no se aprenden"
            )
            continue
        previous = stored.get(key, [])
        merged, action = _merge_registry_anchors(key, previous, observed)
        if merged is None or not action:
            continue
        stored[key] = merged
        counters[action] += 1
    learned = sum(counters.values())
    if learned:
        try:
            _save_book_dates(path, stored)
        except OSError as exc:
            logger.warning(
                f"No se pudo guardar el registro de fechas {path}: {exc}"
            )
            return 0
        logger.info(
            f"Registro de fechas: {counters['nuevo']} libro(s) nuevo(s), "
            f"{counters['ampliado']} ampliado(s), "
            f"{counters['corregido']} corregido(s), "
            f"{len(stored)} total, {path.stat().st_size} bytes"
        )
    return learned


def correct_dates_by_book(
    reports: List[ValidationReport],
    book_dates_path: Optional[Path] = None,
) -> Dict[str, int]:
    """Completa mes y año por ``log_number`` sin alterar el día OCR.

    Args:
        reports: Reportes ya validados (uno por PDF procesado).
        book_dates_path: Registro de extremos de fecha aprendidos en otras
            ejecuciones. Si se omite, el corrector solo ve la ejecución
            actual, que es el comportamiento aislado de siempre.

    El resultado ``corrected`` cuenta componentes inferidos o corregidos,
    no paginas.
    ``days_filled`` cuenta los días completados con el último día que cabe en
    la secuencia del libro.
    """
    books = group_books(reports)
    stored = (
        _load_book_dates(Path(book_dates_path))
        if book_dates_path is not None
        else {}
    )
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
        "registry_filled": 0,
        "unresolved": 0,
    }

    for book in books:
        for page in book:
            _recombine(page)

        year_consensus = _correct_year_by_book_consensus(book)
        stats["years_consensus"] += year_consensus
        sequence_candidates = _resolve_sequence_alternatives(book)
        stats["sequence_candidates"] += sequence_candidates

        # Las anclas del libro se fotografían antes de interpolar: después,
        # una lectura en conflicto con un intervalo ya no cuenta como ancla
        # y el libro parecería unánime sin serlo.
        year_anchors = _anchors(book, YEAR_FIELD_ID, _year_normalize)
        month_anchors = _anchors(book, "month", _month_number)

        years, year_flags = _infer_between_anchors(
            book, YEAR_FIELD_ID, _year_normalize
        )
        years += _infer_edges(book, YEAR_FIELD_ID, _year_normalize)
        years += _fill_from_book_consensus(
            book, YEAR_FIELD_ID, _year_normalize, year_anchors
        )
        months, month_flags = _infer_between_anchors(
            book, "month", _month_number
        )
        months += _infer_edges(book, "month", _month_number)
        months += _fill_from_book_consensus(
            book, "month", _month_number, month_anchors
        )

        # Lo último que se consulta es el registro de otras ejecuciones: la
        # evidencia de las páginas que están aquí siempre manda sobre lo
        # guardado.
        registry_key = _storage_key(book)
        registry_filled = (
            _fill_from_registry(book, registry_key, stored[registry_key])
            if registry_key is not None and registry_key in stored
            else 0
        )
        stats["registry_filled"] += registry_filled

        for page in book:
            _recombine(page)
        days = _fill_days_to_month_end(book)
        for page in book:
            _recombine(page)
        regressions = _check_regressions(book)
        unresolved = _flag_unresolved(book)

        stats["years_filled"] += years
        stats["months_filled"] += months
        stats["days_filled"] += days
        stats["corrected"] += (
            years + months + days + year_consensus + sequence_candidates
            + registry_filled
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

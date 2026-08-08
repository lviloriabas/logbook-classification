"""Corrector de fechas por libro (bitácoras secuenciales).

Las bitácoras de un libro se llenan en secuencia: el ``log_number``
crece de uno en uno (tolerando páginas faltantes), varias bitácoras
pueden tener la misma fecha (mismo día, varios vuelos) y ningún día
puede quedar fuera de orden. Este corrector aprovecha esas restricciones
para detectar errores y corregir lecturas débiles:

1. **Normalización de año por votación**: los años de 3 dígitos
   ("216") se corrigen al ganador de 2 dígitos del libro ("26") si el
   dígito extra es una inserción (misma secuencia). Los años vacíos o
   ilegibles del libro se rellenan con el ganador. Los años de 4
   dígitos votan normalizados ("2026" -> "26").
2. **Relleno de meses por votación**: cuando la página ya tiene día y
   año resueltos pero el mes queda vacío, se rellena con el mes
   mayoritario del libro (exige >= 3 votos y >= 60% de los meses
   legibles; conservador ante libros que cruzan de mes).
3. **Runs de misma fecha**: las páginas consecutivas con la misma
   fecha forman un "run". Un run es sólido con >= 3 votos. Las páginas
   del run sin fecha legible se corrigen a la fecha del run; las que
   tienen una fecha distinta se marcan como discrepantes (WARNING, no
   se sobrescriben). También se cubren las páginas iniciales del libro
   sin fecha (el libro arranca el día del primer run sólido) y las
   páginas aisladas entre dos fechas idénticas (sandwich).
4. **Monotonicidad**: las fechas efectivas nunca pueden decrecer
   (fecha[i] <= fecha[i+1]); una regresión se marca como WARNING.
5. **Última red / nunca vacía**: las páginas con mes y año resueltos
   a las que falte solo el día se completan con la moda de día del
   libro (>= 3 votos). Si tras todo queda una página sin fecha, se
   marca ERROR explícito (nunca un campo vacío silencioso). Las fechas
   inferidas llevan confianza calculada por votos (0.60-0.95).
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from loguru import logger

from app.models.schemas import PageResult, Status, ValidationReport
from app.utils.postprocess import MESES, _parse_month, combine_date
from app.validation.book_corrector import _recompute_page_status, _recompute_summary
from app.validation.grouping import group_books

DATE_FIELD_IDS = ("day", "month", "year")
YEAR_FIELD_ID = "year"
DATE_RE = re.compile(r"^\d{4}/\d{2}/\d{2}$")

# Votos de fecha idéntica para que un run sea sólido (mayoría alta).
MIN_RUN_VOTES = 3
# Votos para normalizar un año de 3 dígitos contra el ganador del libro.
MIN_YEAR_VOTES = 2

_ORDER = {Status.OK: 0, Status.WARNING: 1, Status.ERROR: 2}


def _field(page: PageResult, field_id: str):
    for field in page.fields:
        if field.field_id == field_id:
            return field
    return None


def _combined(page: PageResult) -> Optional[str]:
    """Fecha combinada YYYY/MM/dd de la página, o None si no es válida.

    La fecha vive en ``page.date`` (el campo ``year`` conserva solo el
    año de 2-4 dígitos). Como respaldo se acepta un ``year`` que ya
    contenga una fecha completa.
    """
    if page.date:
        return page.date if DATE_RE.match(page.date) else None
    year = _field(page, YEAR_FIELD_ID)
    if year is None or not year.value:
        return None
    value = year.value.strip()
    return value if DATE_RE.match(value) else None


def _parse_date(value: str) -> Optional[Tuple[int, int, int]]:
    try:
        return tuple(map(int, value.split("/")))  # type: ignore[return-value]
    except (ValueError, AttributeError):
        return None


def _format_month(mes: int) -> str:
    for nombre, numero in MESES.items():
        if numero == mes:
            return nombre
    return str(mes)


# ── Año ────────────────────────────────────────────────────────────────

def _year_votes(book: List[PageResult]) -> Dict[str, int]:
    """Votos por par de dígitos de año (2026 -> 26, 216 queda como está)."""
    votes: Dict[str, int] = {}
    for page in book:
        year = _field(page, YEAR_FIELD_ID)
        if year is None or not year.value:
            continue
        digits = re.sub(r"[^\d]", "", year.value)
        if len(digits) == 4:
            digits = digits[-2:]
        if len(digits) == 2:
            votes[digits] = votes.get(digits, 0) + 1
    return votes


def _match_3_digit(digits: str, winner: str) -> bool:
    """True si quitando UN dígito de ``digits`` queda ``winner``."""
    if len(digits) != 3 or len(winner) != 2:
        return False
    return any(digits[:i] + digits[i + 1:] == winner for i in range(3))


def _normalize_years(book: List[PageResult]) -> int:
    """Corrige años de 3 dígitos al par ganador del libro, rellena los
    años vacíos/ilegibles y marca como WARNING los años válidos que
    difieran del ganador.

    Devuelve el número de años corregidos o rellenados (y recombina las
    fechas afectadas). Las lecturas válidas discrepantes solo se marcan
    (no se sobrescriben): un libro puede cruzar de año.
    """
    votes = _year_votes(book)
    ranked = sorted(votes.items(), key=lambda item: item[1], reverse=True)
    if not ranked or ranked[0][1] < MIN_YEAR_VOTES:
        return 0
    winner, count = ranked[0]

    corrected = 0
    flagged = 0
    for page in book:
        year = _field(page, YEAR_FIELD_ID)
        if year is None:
            continue
        digits = re.sub(r"[^\d]", "", year.value or "")
        if len(digits) == 4:
            normalized = digits[-2:]
        else:
            normalized = digits

        if not year.value:
            year.value = winner
            year.status = Status.OK
            year.confidence = 0.90
            year.comment = (
                f"Inferred from book readings: {winner} ({count} vote(s))"
            )
            _recombine(page)
            corrected += 1
        elif year.status is Status.ERROR and _match_3_digit(digits, winner):
            year.value = winner
            year.status = Status.OK
            year.confidence = 0.90
            year.comment = (
                f"Inferred from book readings: {winner} ({count} vote(s))"
            )
            _recombine(page)
            corrected += 1
        elif (
            year.status is Status.OK
            and count >= MIN_RUN_VOTES
            and normalized != winner
        ):
            year.status = Status.WARNING
            year.comment = (
                f"Year differs from book majority: {winner} ({count} vote(s))"
            )
            flagged += 1
    logger.debug(f"[Libro] Años: ganador {winner} ({count}) | "
                 f"corregidos/rellenados: {corrected} | marcados: {flagged}")
    return corrected


# ── Mes ────────────────────────────────────────────────────────────────

def _month_number(value: str) -> Optional[int]:
    """Número de mes (1-12) desde dígitos o letras (normalizado)."""
    raw = re.sub(r"[^\dA-Za-z]", "", value).upper()
    if not raw:
        return None
    if not re.search(r"[A-Za-z]", raw):
        digits = re.sub(r"[^\d]", "", raw)
        if not digits:
            return None
        mes = int(digits[:3])
        return mes if 1 <= mes <= 12 else None
    return _parse_month(raw)


def _fill_months(book: List[PageResult]) -> int:
    """Rellena meses vacíos con el mes mayoritario del libro, solo cuando
    la página ya tiene día y año resueltos (la fecha queda completa).

    Conservador: exige >= MIN_RUN_VOTES votos al mes ganador y que este
    concentre >= 60% de los meses legibles del libro, para no corregir
    mal páginas de libros que cruzan de mes.
    """
    votes: Dict[int, int] = {}
    for page in book:
        month = _field(page, "month")
        if month is None or not month.value:
            continue
        num = _month_number(month.value)
        if num:
            votes[num] = votes.get(num, 0) + 1
    ranked = sorted(votes.items(), key=lambda item: item[1], reverse=True)
    if not ranked or ranked[0][1] < MIN_RUN_VOTES:
        return 0
    best, count = ranked[0]
    total = sum(votes.values())
    if total and count / total < 0.6:
        return 0
    filled = 0
    for page in book:
        day = _field(page, "day")
        month = _field(page, "month")
        year = _field(page, YEAR_FIELD_ID)
        if day is None or month is None or year is None:
            continue
        if month.value or not day.value or not year.value:
            continue
        month.value = _format_month(best)
        month.status = Status.OK
        month.confidence = _inferred_confidence(count)
        month.comment = (
            f"Inferred from book months: {_format_month(best)} "
            f"({count} vote(s))"
        )
        _recombine(page)
        filled += 1
    if filled:
        logger.debug(f"[Libro] Meses: ganador {_format_month(best)} "
                     f"({count}) | rellenados: {filled}")
    return filled


# ── Runs de misma fecha ────────────────────────────────────────────────

def _solid_runs(book: List[PageResult]) -> List[Tuple[int, int, str, int]]:
    """Runs de fecha idéntica consecutiva (solo páginas con fecha válida).

    Returns:
        Lista de (índice inicial, índice final, fecha, número de votos).
    """
    dates = [
        (index, _combined(page))
        for index, page in enumerate(book)
        if _combined(page)
    ]
    runs: List[Tuple[int, int, str, int]] = []
    index = 0
    while index < len(dates):
        end = index
        while end < len(dates) and dates[end][1] == dates[index][1]:
            end += 1
        runs.append((dates[index][0], dates[end - 1][0],
                     dates[index][1], end - index))  # type: ignore[arg-type]
        index = end
    return runs


def _inferred_confidence(votes: int) -> float:
    """Confianza de una fecha inferida por votos del libro.

    La inferencia secuencial (runs de fecha idéntica, mayoría de mes/año)
    es más fiable cuantos más votos la respalden, pero nunca alcanza la
    de una lectura directa: se acota en [0.65, 0.95].
    """
    return round(min(0.95, 0.60 + votes * 0.07), 3)


def _set_inferred(page: PageResult, fecha: str, votes: int) -> None:
    """Sobrescribe la fecha de una página ilegible con la del run."""
    parsed = _parse_date(fecha)
    if parsed is None:
        return
    anio, mes, dia = parsed
    day = _field(page, "day")
    month = _field(page, "month")
    year = _field(page, YEAR_FIELD_ID)
    if year is None:
        return
    conf = _inferred_confidence(votes)
    for field, value in ((day, str(dia)), (month, _format_month(mes)),
                         (year, f"{anio % 100:02d}")):
        if field is None:
            continue
        field.value = value
        field.status = Status.OK
        field.confidence = conf
        field.comment = ""
    page.date = fecha
    year.comment = f"Inferred from book dates: {fecha} ({votes} vote(s))"


def _flag_differs(page: PageResult, fecha: str, votes: int) -> None:
    year = _field(page, YEAR_FIELD_ID)
    if year is None:
        return
    year.status = Status.WARNING
    year.comment = f"Date differs from book majority: {fecha} ({votes} vote(s))"


def _correct_runs(book: List[PageResult]) -> Tuple[int, int]:
    """Corrige/marca las fechas dentro de runs sólidos. Devuelve
    (corregidas, discrepantes marcadas).

    Cada run sólido "cubre" su páginas y las ilegibles hasta el inicio
    del siguiente run: las páginas sin fecha válida dentro de esa
    cobertura se infieren del run; las que tienen una fecha distinta se
    marcan (WARNING, sin sobrescribir). También se cubren las páginas
    iniciales del libro sin fecha (si un libro arranca con un run sólido,
    el comienzo debió pertenecer a ese mismo día) y las páginas aisladas
    entre dos fechas idénticas (sandwich).
    """
    corrected = 0
    flagged = 0
    runs = _solid_runs(book)
    for index, (start, _end, fecha, votes) in enumerate(runs):
        if votes < MIN_RUN_VOTES:
            continue
        cover_start = 0 if index == 0 else runs[index - 1][1] + 1
        cover_end = runs[index + 1][0] - 1 if index + 1 < len(runs) \
            else len(book) - 1
        for page in book[cover_start:cover_end + 1]:
            current = _combined(page)
            if current == fecha:
                continue
            if current is None:
                _set_inferred(page, fecha, votes)
                corrected += 1
            else:
                _flag_differs(page, fecha, votes)
                flagged += 1
    corrected += _fill_sandwiches(book)
    return corrected, flagged


def _fill_sandwiches(book: List[PageResult]) -> int:
    """Rellena páginas sin fecha entre dos fechas válidas idénticas.

    Si la página anterior y la posterior tienen la MISMA fecha válida y
    la página del medio no tiene ninguna, la inferencia es segura
    (secuencialidad de la bitácora): se rellena con esa fecha.
    """
    filled = 0
    for i in range(1, len(book) - 1):
        page = book[i]
        if page.blank or _combined(page) is not None:
            continue
        before = _combined(book[i - 1])
        after = _combined(book[i + 1])
        if before == after and before is not None:
            votes = 2
            _set_inferred(page, before, votes)
            filled += 1
    return filled


# ── Relleno de día por mayoría y aviso de fechas sin resolver ─────────

def _mode_day(book: List[PageResult]) -> Optional[Tuple[str, int]]:
    """Moda del día (1-31) entre las páginas con día legible.

    Solo devuelve una moda útil si hay suficiente respaldo (>= 3 votos);
    la fecha completa NO se fabrica: solo se rellena el día cuando la
    página ya tiene mes y año.
    """
    votes: Dict[str, int] = {}
    for page in book:
        day = _field(page, "day")
        if day is None or not day.value:
            continue
        digits = re.sub(r"[^\d]", "", day.value)
        if digits.isdigit() and 1 <= int(digits) <= 31:
            votes[int(digits)] = votes.get(int(digits), 0) + 1
    ranked = sorted(votes.items(), key=lambda item: item[1], reverse=True)
    if not ranked or ranked[0][1] < MIN_RUN_VOTES:
        return None
    best, count = ranked[0]
    return f"{best:02d}", count


def _fill_remaining_days(book: List[PageResult]) -> int:
    """Rellena el día de páginas que ya tienen mes y año resueltos.

    Ultima red: si una mayoría de páginas del libro comparte el mismo día
    leído (>= MIN_RUN_VOTES), las páginas a las que solo falta el día se
    completan con la moda y se marcan como inferidas (verificables).
    """
    mode = _mode_day(book)
    if mode is None:
        return 0
    value, count = mode
    filled = 0
    for page in book:
        day = _field(page, "day")
        month = _field(page, "month")
        year = _field(page, YEAR_FIELD_ID)
        if day is None or month is None or year is None:
            continue
        if day.value or not month.value or not year.value:
            continue
        day.value = value
        day.status = Status.OK
        day.confidence = _inferred_confidence(count)
        day.comment = (
            f"Inferred from book days: {value} ({count} vote(s))"
        )
        _recombine(page)
        filled += 1
    return filled


def _flag_unresolved(book: List[PageResult]) -> int:
    """Marca ERROR las páginas que quedaron sin fecha tras las inferencias.

    La fecha nunca se reporta vacía en silencio: si tras OCR + correctores
    no hay ni lectura ni inferencia, el campo año queda en ERROR con una
    nota explícita para revisión (indexación manual).
    """
    unresolved = 0
    for page in book:
        if page.blank or _combined(page) is not None:
            continue
        for field_id in ("day", "month", YEAR_FIELD_ID):
            field = _field(page, field_id)
            if field is not None and not field.value:
                field.status = Status.ERROR
                field.comment = (
                    "Fecha sin resolver tras OCR y correctores de secuencia"
                )
                unresolved += 1
    return unresolved


# ── Monotonicidad ──────────────────────────────────────────────────────

def _check_regressions(book: List[PageResult]) -> int:
    """Marca WARNING las fechas que decrecen respecto a la anterior."""
    regressions = 0
    previous: Optional[Tuple[int, int, int]] = None
    for page in book:
        if page.blank:
            continue
        combined = _combined(page)
        if combined is None:
            continue
        parsed = _parse_date(combined)
        if parsed is None:
            continue
        if previous is not None and parsed < previous:
            year = _field(page, YEAR_FIELD_ID)
            if year is not None and year.status is not Status.ERROR:
                year.status = Status.WARNING
                suffix = f" | Date regression vs previous entry"
                year.comment = (year.comment + suffix
                                if year.comment else suffix.lstrip())
                regressions += 1
        previous = parsed
    return regressions


# ── Orquestador ────────────────────────────────────────────────────────

def correct_dates_by_book(
    reports: List[ValidationReport],
) -> Dict[str, int]:
    """Corrector global de fechas (bitácoras secuenciales por libro).

    Args:
        reports: Reportes ya validados (uno por PDF procesado).

    Returns:
        Estadísticas: libros, corregidas, marcadas, regresiones.
    """
    books = group_books(reports)
    stats = {"books": len(books), "corrected": 0, "flagged": 0,
             "regressions": 0, "months_filled": 0, "unresolved": 0}
    for book in books:
        if not book:
            continue
        corrected_years = _normalize_years(book)
        filled_months = _fill_months(book)
        # Páginas con las tres partes legibles (day+month+year) cuya
        # fecha completa aún no está combinada (p. ej. sin pasar por
        # pipeline._combine_date_parts): combinarla evita que un run sólido
        # sobrescriba una fecha válida distinta. Un año ilegible (status
        # ERROR) se deja sin combinar para que el run la infiera; uno
        # legible aunque discrepante (WARNING) se combina y el run solo la
        # marca.
        for page in book:
            if page.date is None:
                anio = _field(page, YEAR_FIELD_ID)
                if anio is not None and anio.status is not Status.ERROR:
                    _recombine(page)
        corrected, flagged = _correct_runs(book)
        filled_days = _fill_remaining_days(book)
        regressions = _check_regressions(book)
        unresolved = _flag_unresolved(book)
        stats["corrected"] += corrected_years + corrected + filled_months
        stats["flagged"] += flagged
        stats["regressions"] += regressions
        stats["months_filled"] += filled_months
        stats["days_filled"] = stats.get("days_filled", 0) + filled_days
        stats["unresolved"] += unresolved
        for page in book:
            _recompute_page_status(page)
    for report in reports:
        _recompute_summary(report)
    logger.info(f"Corrector de fechas: {stats}")
    return stats


def _recombine(page: PageResult) -> None:
    """Recombina day/month/year tras corregir el año (comportamiento
    equivalente a pipeline._combine_date_parts). El campo ``year``
    conserva el año corregido; la fecha completa va a ``page.date``."""
    day = _field(page, "day")
    month = _field(page, "month")
    year = _field(page, YEAR_FIELD_ID)
    if not (day and month and year):
        return
    combined, note = combine_date(day.value, month.value, year.value)
    if note:
        year.comment = note if not year.comment else f"{year.comment} | {note}"
        page.date = None
    else:
        page.date = combined

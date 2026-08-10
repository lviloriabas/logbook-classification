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


def _votes_canonical(book: List[PageResult], field_id: str,
                     normalize) -> Dict[str, int]:
    """Cuenta los valores legibles de un campo del libro, normalizados.

    ``normalize`` debe mapear el valor bruto a la forma canónica
    (p. ej. día "2" → "02", mes "JUL" → "JUL", año "26" → "26"). Si el
    normalizador devuelve None, el voto se descarta (ruido).
    """
    votes: Dict[str, int] = {}
    for page in book:
        f = _field(page, field_id)
        if f is None or not f.value:
            continue
        canon = normalize(f.value)
        if canon is None:
            continue
        votes[canon] = votes.get(canon, 0) + 1
    return votes


def _day_normalize(raw: str) -> Optional[str]:
    digits = re.sub(r"[^\d]", "", raw)
    if not digits:
        return None
    n = int(digits)
    if 1 <= n <= 31:
        return f"{n:02d}"
    return None


def _year_normalize(raw: str) -> Optional[str]:
    digits = re.sub(r"[^\d]", "", raw)
    if len(digits) == 4:
        digits = digits[-2:]
    if len(digits) == 2:
        return digits
    return None


def _fill_field(book: List[PageResult], field_id: str, normalize,
                comment_prefix: str,
                min_votes: int = 2,
                min_ratio: float = 0.6) -> int:
    """Rellena la pieza que falta (día/mes/año) por votación del libro.

    Itera hasta converger: en cada vuelta cuenta los votos del campo
    (solo páginas donde está legible) y rellena las páginas donde esa
    pieza está vacía pero las otras dos ya están resueltas (o se
    acaban de rellenar en la misma vuelta). Esto cubre el caso
    frecuente de bitácoras donde cada página solo lee una pieza
    correctamente: tras varias vueltas, todas las páginas quedan con
    la pieza resuelta por mayoría.

    ``min_votes`` y ``min_ratio`` son más permisivos que el run sólido
    porque aquí rellenamos piezas que faltan, no corregimos lecturas
    válidas distintas.
    """
    total_filled = 0
    for _ in range(4):  # varias iteraciones: cada vuelta puede destapar
        votes = _votes_canonical(book, field_id, normalize)
        ranked = sorted(votes.items(), key=lambda item: item[1], reverse=True)
        if not ranked or ranked[0][1] < min_votes:
            break
        best, count = ranked[0]
        total = sum(votes.values())
        if total and count / total < min_ratio:
            break
        filled = 0
        for page in book:
            f = _field(page, field_id)
            if f is None or f.value:
                continue
            # Acepta la página si al menos UNA de las otras dos piezas
            # está resuelta; las que falten se llenarán en otra vuelta
            # de _fill_field. Así, con una sola pieza legible por
            # página, las tres se completan por iteración.
            other_ids = [i for i in ("day", "month", YEAR_FIELD_ID) if i != field_id]
            other1 = _field(page, other_ids[0])
            other2 = _field(page, other_ids[1])
            if other1 is None or other2 is None:
                continue
            if not other1.value and not other2.value:
                continue
            f.value = best
            f.status = Status.OK
            f.confidence = _inferred_confidence(count)
            f.comment = (
                f"{comment_prefix}: {best} ({count} vote(s))"
            )
            _recombine(page)
            filled += 1
        if filled == 0:
            break
        total_filled += filled
        logger.debug(f"[Libro] {field_id}: ganador {best} ({count}) | "
                     f"rellenados: {filled}")
    return total_filled


def _fill_missing_pieces(book: List[PageResult]) -> int:
    """Rellena por votación del libro la pieza que falta en una página
    cuyas otras dos ya están resueltas: día, mes o año. Itera entre
    los tres campos hasta que ninguno avance más. Para el año se
    acepta 1 voto (es la pieza más estable dentro de un libro) y para
    día/mes se exigen 2 votos (las fechas pueden cruzar de mes/día)."""
    filled = 0
    changed = True
    while changed:
        changed = False
        for field_id, normalize, prefix, min_votes in (
            ("day", _day_normalize, "Inferred from book days", 2),
            ("month",
             lambda v: (lambda n: _format_month(n) if n else None)(_month_number(v)),
             "Inferred from book months", 2),
            (YEAR_FIELD_ID, _year_normalize,
             "Inferred from book years", 1),
        ):
            n = _fill_field(book, field_id, normalize, prefix,
                            min_votes=min_votes)
            filled += n
            if n > 0:
                changed = True
    return filled


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


MIN_RUN_VOTES = 3
# Para runs de libro se permite bajar a 2 votos cuando el corrector
# no encuentra ningún run de 3 pero sí varios pares de fechas idénticas;
# los warnings de "date differs from book majority" siguen protegiendo
# contra la sobrescritura agresiva.
MIN_RUN_VOTES_FALLBACK = 2


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
    # Si no hay run de 3 votos, bajamos el umbral a 2 para detectar
    # al menos pares sólidos (libros con bitácoras aisladas o
    # bitácoras donde el OCR leyó muy pocas fechas).
    min_votes = MIN_RUN_VOTES
    if not any(votes >= MIN_RUN_VOTES for _, _, _, votes in runs):
        if any(votes >= MIN_RUN_VOTES_FALLBACK for _, _, _, votes in runs):
            min_votes = MIN_RUN_VOTES_FALLBACK
    for index, (start, _end, fecha, votes) in enumerate(runs):
        if votes < min_votes:
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


# ── Normalización de día/mes por mayoría fuerte ──────────────────────

def _normalize_month_majority(book: List[PageResult]) -> int:
    """Sobrescribe el mes de páginas con lectura débil cuando hay una
    mayoría fuerte en el libro.

    Mismo criterio que ``_normalize_day_majority``: mayoría >= 4 votos
    y >= 60% de los meses legibles, segunda opción <= 1 voto. Solo
    cambia un mes claramente erróneo (p. ej. ``1`` cuando la mayoría
    es ``JUL``); un mes distinto con 2+ votos (DIC, AGO) no se toca.
    """
    def normalize(v):
        n = _month_number(v)
        return _format_month(n) if n else None
    votes = _votes_canonical(book, "month", normalize)
    if not votes:
        return 0
    ranked = sorted(votes.items(), key=lambda item: item[1], reverse=True)
    best, count = ranked[0]
    if count < 4:
        return 0
    total = sum(votes.values())
    if total and count / total < 0.6:
        return 0
    if len(ranked) < 2:
        return 0
    second, scount = ranked[1]
    if scount > 1:
        return 0
    overwritten = 0
    for page in book:
        month = _field(page, "month")
        if month is None or not month.value:
            continue
        canon = normalize(month.value)
        if canon is None or canon == best:
            continue
        month.value = best
        month.status = Status.WARNING
        month.confidence = _inferred_confidence(count)
        month.comment = (
            f"Overridden by book month majority: {best} "
            f"({count} vs {second}={scount})"
        )
        _recombine(page)
        overwritten += 1
    if overwritten:
        logger.debug(f"[Libro] Month majority override: {best} ({count}) | "
                     f"sobrescritas: {overwritten}")
    return overwritten


def _normalize_day_majority(book: List[PageResult]) -> int:
    """Sobrescribe el día de páginas con lectura débil cuando hay una
    mayoría fuerte en el libro.

    Condiciones (todas):
      - La mayoría clara: >= 4 votos y >= 60% de los días legibles.
      - El "ruido" es muy débil: la segunda opción tiene <= 1 voto.

    Cuando el OCR devuelve el día correcto en 4+ páginas pero se
    equivoca en 1-2 (lee solo el "0" como "2", o confunde con el "2"
    de otro día), este paso alinea las lecturas erráticas con la
    mayoría. La página sobrescrita queda en WARNING con la nota del
    voto, NO en OK.

    Es agresivo: para evitar falsos positivos, se exige un margen
    muy grande entre la mayoría y la segunda opción. Si la segunda
    opción tiene 2+ votos, las lecturas distintas probablemente son
    intencionales (libro cruza de día, no error de OCR).
    """
    votes = _votes_canonical(book, "day", _day_normalize)
    if not votes:
        return 0
    ranked = sorted(votes.items(), key=lambda item: item[1], reverse=True)
    best, count = ranked[0]
    if count < 4:
        return 0
    total = sum(votes.values())
    if total and count / total < 0.6:
        return 0
    if len(ranked) < 2:
        return 0
    second, scount = ranked[1]
    if scount > 1:
        return 0
    overwritten = 0
    for page in book:
        day = _field(page, "day")
        if day is None or not day.value:
            continue
        canon = _day_normalize(day.value)
        if canon is None or canon == best:
            continue
        day.value = best
        day.status = Status.WARNING
        day.confidence = _inferred_confidence(count)
        day.comment = (
            f"Overridden by book day majority: {best} "
            f"({count} vs {second}={scount})"
        )
        _recombine(page)
        overwritten += 1
    if overwritten:
        logger.debug(f"[Libro] Day majority override: {best} ({count}) | "
                     f"sobrescritas: {overwritten}")
    return overwritten


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

    Adicionalmente, las páginas con día vacío rodeadas por dos páginas
    con día legible idéntico (vecindad) se rellenan: en una bitácora
    secuencial, si la anterior y la siguiente son 20/07, la del medio
    también lo es aunque el OCR no haya leído el día.
    """
    mode = _mode_day(book)
    filled = 0
    if mode is not None:
        value, count = mode
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
    # Vecindad: si el día anterior y el siguiente de la página son el
    # mismo, esta también lo es (la OCR falló).
    for i in range(1, len(book) - 1):
        page = book[i]
        if page.blank:
            continue
        day = _field(page, "day")
        month = _field(page, "month")
        year = _field(page, YEAR_FIELD_ID)
        if day is None or month is None or year is None:
            continue
        if day.value or not month.value or not year.value:
            continue
        prev_day = _field(book[i - 1], "day")
        next_day = _field(book[i + 1], "day")
        if prev_day is None or next_day is None:
            continue
        if prev_day.value and next_day.value and prev_day.value == next_day.value:
            value = prev_day.value
            day.value = value
            day.status = Status.OK
            day.confidence = round(
                min(prev_day.confidence, next_day.confidence), 3)
            day.comment = (
                f"Inferred from neighbors: {value} (sandwich)"
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
        filled_pieces = _fill_missing_pieces(book)
        filled_days = _fill_remaining_days(book)
        normalized_days = _normalize_day_majority(book)
        normalized_months = _normalize_month_majority(book)
        regressions = _check_regressions(book)
        unresolved = _flag_unresolved(book)
        stats["corrected"] += (corrected_years + corrected + filled_months
                               + filled_pieces + filled_days
                               + normalized_days + normalized_months)
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

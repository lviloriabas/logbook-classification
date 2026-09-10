"""Marca temporal de revisión sin alterar la fecha reconocida.

Solo la antigüedad aparta una página. Una fecha posterior a la ejecución no
llega hasta aquí: el corrector de fechas aparta esa lectura y la sustituye
por el día que el libro admite, así que la bitácora se indexa sola en vez de
pasar a REVISAR por un número mal leído. Lo que se revisa es lo que ninguna
inferencia puede arreglar: una fecha anterior al año de la ejecución, salvo
la cola del año anterior que todavía es normal durante enero.

Tampoco se revisa un libro viejo de verdad. Si dos bitácoras distintas del
mismo libro leyeron ese año, no es un año mal leído: es un libro atrasado, y
mandarlo entero a REVISAR obligaba a teclear a mano páginas cuyos datos ya
estaban bien. Lo que queda apartado es el año que solo sostiene una lectura.
"""

from __future__ import annotations

from datetime import date
from typing import Collection, Iterable, Optional, Sequence

from app.models.schemas import PageResult, ValidationReport
from app.utils.date_window import reference_date, review_start
from app.utils.postprocess import _parse_month
from app.validation.grouping import group_books, log_number

_OLD = "Fecha muy antigua:"
# Avisos de reglas anteriores: el periodo habitual como borde de la revisión
# y la fecha futura como motivo para apartar. Se siguen reconociendo para
# poder quitarlos al volver a exportar una ejecución guardada; si no, la
# página conservaría el motivo de una regla que ya no la aparta.
_OWNED = (_OLD, "Fecha futura:", "Fecha fuera del periodo habitual:")

# Días que el programa escribe porque nadie los leyó: la ejecución a fin de
# mes (``month_end_policy``) y el relleno con el último día que cabe en el
# libro (``month_end_fallback``). Una fecha así solo se puede comprobar por
# mes: tratarla como escrita la hacía «futura» por unos días y mandaba a
# REVISAR bitácoras cuyo mes y año estaban perfectamente leídos.
_UNREAD_DAY_METHODS = frozenset({"month_end_policy", "month_end_fallback"})

# Bitácoras distintas del libro que tienen que leer el mismo año para darlo
# por el año del libro y no por una lectura suelta.
MIN_BOOK_YEAR_READINGS = 2
# Un año que puso un corrector no es una lectura: no puede respaldarse a sí
# mismo.
_DERIVED_SOURCES = frozenset({"inferred", "book_correction"})


def _year_of(value: Optional[str]) -> Optional[int]:
    text = (value or "").strip()
    if not text.isdigit() or len(text) not in (2, 4):
        return None
    return 2000 + int(text) if len(text) == 2 else int(text)


def book_years(book: Iterable[PageResult]) -> frozenset[int]:
    """Años que el libro leyó directamente en dos bitácoras distintas.

    Se cuentan números de bitácora, no filas: la misma página escaneada en
    dos PDF es una sola lectura. Sin ``log_number`` no se sabe a qué libro
    pertenece la página, así que tampoco aporta.
    """
    readings: dict[int, set[int]] = {}
    for page in book:
        number = log_number(page)
        if page.blank or number is None:
            continue
        field = next(
            (item for item in page.fields if item.field_id == "year"), None
        )
        if field is None or field.source in _DERIVED_SOURCES:
            continue
        year = _year_of(field.value)
        if year is not None:
            readings.setdefault(year, set()).add(number)
    return frozenset(
        year for year, numbers in readings.items()
        if len(numbers) >= MIN_BOOK_YEAR_READINGS
    )


def date_window_issue(
    page: PageResult,
    today: Optional[date] = None,
    supported_years: Collection[int] = frozenset(),
) -> str:
    """Motivo temporal; vacío si la fecha entra en el periodo permitido.

    ``supported_years`` son los años que el libro de la página respalda
    (ver :func:`book_years`): una fecha antigua de uno de ellos no se aparta.
    """
    if page.blank:
        return ""
    fields = {field.field_id: field for field in page.fields}
    reference = reference_date(today)
    day_field = fields.get("day")
    month_only = bool(
        day_field and day_field.inference_method in _UNREAD_DAY_METHODS
    )
    try:
        if page.date:
            year, month, day = map(int, page.date.replace("-", "/").split("/"))
        else:
            year_text = fields["year"].value or fields["year"].raw_value or ""
            if len(year_text) not in (2, 4) or not year_text.isdigit():
                return ""
            year = int(year_text)
            year = 2000 + year if len(year_text) == 2 else year
            month = _parse_month(fields["month"].value or "")
            if month is None:
                return ""
            day_text = day_field.value if day_field else None
            day = int(day_text) if day_text else 1
        value = date(year, month, 1 if month_only else day)
    except (KeyError, TypeError, ValueError):
        return ""
    start = review_start(reference)
    if value < start and value.year not in supported_years:
        return f"{_OLD} {value:%Y/%m/%d}, anterior a {start:%Y/%m/%d}"
    return ""


def review_date_window(
    page: PageResult,
    today: Optional[date] = None,
    supported_years: Collection[int] = frozenset(),
) -> bool:
    """Actualiza solo su aviso y respeta los otros motivos de revisión."""
    comments = page.comment.split(" | ") if page.comment else []
    owned = any(part.startswith(_OWNED) for part in comments)
    comments = [part for part in comments if not part.startswith(_OWNED)]
    issue = date_window_issue(page, today, supported_years)
    other_review = any(field.inference_method == "run_year_review" for field in page.fields)
    page.date_review = bool(issue or other_review or (page.date_review and not owned))
    if issue:
        comments.append(issue)
    page.comment = " | ".join(comments)
    return bool(issue)


def review_date_windows(
    reports: Sequence[ValidationReport], today: Optional[date] = None
) -> int:
    """Revisa la antigüedad de toda la ejecución con cada libro a la vista.

    Es la versión que deciden las salidas: una página sola no sabe si su año
    lo leyeron también otras bitácoras de su libro. Devuelve cuántas quedaron
    apartadas por antigüedad.
    """
    flagged = 0
    for book in group_books(list(reports)):
        years = book_years(book)
        for page in book:
            flagged += review_date_window(page, today, years)
    return flagged

"""Marca temporal de revisión sin alterar la fecha reconocida."""

from __future__ import annotations

from datetime import date
from typing import Optional

from app.models.schemas import PageResult
from app.utils.date_window import reference_date, review_start
from app.utils.postprocess import _parse_month

_OLD = "Fecha muy antigua:"
_FUTURE = "Fecha futura:"
_FUTURE_METHODS = {"year_out_of_window", "month_out_of_window", "day_out_of_window"}
# El aviso se llamaba «Fecha fuera del periodo habitual» cuando la revisión
# se medía con el mes anterior. Se sigue reconociendo para poder quitarlo al
# volver a exportar una ejecución guardada: si no, la página conservaría el
# motivo de una regla que ya no la aparta.
_OWNED = (_OLD, _FUTURE, "Fecha fuera del periodo habitual:")

# Días que el programa escribe porque nadie los leyó: la ejecución a fin de
# mes (``month_end_policy``) y el relleno con el último día que cabe en el
# libro (``month_end_fallback``). Una fecha así solo se puede comprobar por
# mes: tratarla como escrita la hacía «futura» por unos días y mandaba a
# REVISAR bitácoras cuyo mes y año estaban perfectamente leídos.
_UNREAD_DAY_METHODS = frozenset({"month_end_policy", "month_end_fallback"})


def date_window_issue(page: PageResult, today: Optional[date] = None) -> str:
    """Motivo temporal; vacío si la fecha entra en el periodo permitido."""
    if page.blank:
        return ""
    fields = {field.field_id: field for field in page.fields}
    if any(field.inference_method in _FUTURE_METHODS and not field.value
           for field in fields.values()):
        return f"{_FUTURE} la lectura pendiente supera el día de ejecución"
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
    if value > reference:
        return f"{_FUTURE} {value:%Y/%m/%d}, posterior a {reference:%Y/%m/%d}"
    start = review_start(reference)
    if value < start:
        return f"{_OLD} {value:%Y/%m/%d}, anterior a {start:%Y/%m/%d}"
    return ""


def review_date_window(page: PageResult, today: Optional[date] = None) -> bool:
    """Actualiza solo su aviso y respeta los otros motivos de revisión."""
    comments = page.comment.split(" | ") if page.comment else []
    owned = any(part.startswith(_OWNED) for part in comments)
    comments = [part for part in comments if not part.startswith(_OWNED)]
    issue = date_window_issue(page, today)
    other_review = any(field.inference_method == "run_year_review" for field in page.fields)
    page.date_review = bool(issue or other_review or (page.date_review and not owned))
    if issue:
        comments.append(issue)
    page.comment = " | ".join(comments)
    return bool(issue)

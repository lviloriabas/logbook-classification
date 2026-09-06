"""Marca temporal de revisión sin alterar la fecha reconocida."""

from __future__ import annotations

from datetime import date
from typing import Optional

from app.models.schemas import PageResult
from app.utils.date_window import reference_date, usual_start
from app.utils.postprocess import _parse_month

_OLD = "Fecha fuera del periodo habitual:"
_FUTURE = "Fecha futura:"
_FUTURE_METHODS = {"year_out_of_window", "month_out_of_window", "day_out_of_window"}


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
    month_only = bool(day_field and day_field.inference_method == "month_end_policy")
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
    start = usual_start(reference)
    if value < start:
        return f"{_OLD} {value:%Y/%m/%d}, anterior a {start:%Y/%m/%d}"
    return ""


def review_date_window(page: PageResult, today: Optional[date] = None) -> bool:
    """Actualiza solo su aviso y respeta los otros motivos de revisión."""
    comments = page.comment.split(" | ") if page.comment else []
    owned = any(part.startswith((_OLD, _FUTURE)) for part in comments)
    comments = [part for part in comments if not part.startswith((_OLD, _FUTURE))]
    issue = date_window_issue(page, today)
    other_review = any(field.inference_method == "run_year_review" for field in page.fields)
    page.date_review = bool(issue or other_review or (page.date_review and not owned))
    if issue:
        comments.append(issue)
    page.comment = " | ".join(comments)
    return bool(issue)

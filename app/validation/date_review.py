"""Marca temporal de revisión sin alterar la fecha reconocida.

Solo la antigüedad aparta una página. Una fecha posterior a la ejecución no
llega hasta aquí: el corrector de fechas aparta esa lectura y la sustituye
por el día que el libro admite, así que la bitácora se indexa sola en vez de
pasar a REVISAR por un número mal leído. Lo que se revisa es lo que ninguna
inferencia puede arreglar: una fecha de hace más de un año, que casi siempre
es el año mal leído y que nadie puede confirmar sin ver la página.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from app.models.schemas import PageResult
from app.utils.date_window import reference_date, review_start
from app.utils.postprocess import _parse_month

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


def date_window_issue(page: PageResult, today: Optional[date] = None) -> str:
    """Motivo temporal; vacío si la fecha entra en el periodo permitido."""
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

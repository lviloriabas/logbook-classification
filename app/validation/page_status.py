"""Estado de una página: qué se pudo indexar y qué queda por revisar.

Una bitácora se indexa con tres datos: el número de bitácora, la matrícula
del avión y la fecha. El estado de la página dice cuánto le falta para
indexarse sola:

- ``ERROR``: no hay nada que indexar. La página está en blanco o ninguno de
  los tres datos salió legible, así que tampoco hay de dónde inferir el
  resto y tiene que leerla una persona.
- ``WARNING``: se pudo indexar, pero algo quedó por confirmar: un dato
  inferido por el libro, una lectura de confianza baja o un dato que sigue
  sin resolverse.
- ``OK``: los tres datos salieron de la lectura directa y ninguno quedó en
  duda.

Dos familias de campos no deciden el estado:

- **Las firmas.** Que una bitácora de vuelo no traiga firma de técnico es lo
  normal, no un error de lectura; quién debía firmar cada tipo de página lo
  juzga ``app.validation.discrepancias``. Contarlas aquí ponía en ERROR a
  casi toda la ejecución por páginas perfectamente indexables.
- **Las celdas de carácter** (``day_1``, ``month_2``…). Son evidencia
  auxiliar de la fecha: si día, mes y año quedaron resueltos, da igual que
  una casilla suelta no se leyera.

El número de vuelo tampoco cuenta: es opcional y no forma parte del índice.
"""

from __future__ import annotations

import re
from typing import List, Optional

from app.models.schemas import FieldResult, PageResult, Status

# Los tres datos con los que se indexa una bitácora.
LOG_NUMBER_FIELD_ID = "log_number"
MATRICULA_FIELD_ID = "matricula"
DATE_FIELD_IDS = ("day", "month", "year")

# Una matrícula impuesta por el consenso necesita más de una lectura física.
# Para las lecturas directas, el ``status`` ya refleja el umbral configurado
# por el usuario; imponer aquí otro piso fijo mandaría páginas válidas a
# Revisar de forma liberal y haría crecer innecesariamente ese batch.
AUTO_INDEX_MIN_VOTES = 2
_INFERRED_MATRICULA_SOURCES = frozenset({
    "book_correction",
    "fleet_validation",
    "inferred",
})

_LOG_NUMBER_RE = re.compile(r"^\d{7}$")
_MATRICULA_RE = re.compile(r"^HP-\d{4}(?:CMP|WWP)$")
_DATE_RE = re.compile(r"^\d{4}/\d{2}/\d{2}$")
_MONTH_RE = re.compile(r"^(?:[A-Z]{3}|\d{1,2})$")
_YEAR_RE = re.compile(r"^\d{2}(?:\d{2})?$")
# Casillas sueltas de la banda de fecha: evidencia, no dato del índice.
_CHAR_CELL_RE = re.compile(r"(?:day|month|year)_\d")
_SIGNATURE_TYPE = "signature"

_ORDER = {Status.OK: 0, Status.WARNING: 1, Status.ERROR: 2}


def field_of(page: PageResult, field_id: str) -> Optional[FieldResult]:
    """Campo de la página con ese identificador, o ``None``."""
    for field in page.fields:
        if field.field_id == field_id:
            return field
    return None


def decisive_fields(page: PageResult) -> List[FieldResult]:
    """Campos que deciden el estado: ni firmas ni celdas de carácter."""
    return [
        field for field in page.fields
        if field.field_type != _SIGNATURE_TYPE
        and not _CHAR_CELL_RE.fullmatch(field.field_id)
        and field.field_id != "flight_number"
    ]


def has_log_number(page: PageResult) -> bool:
    """El número de bitácora quedó legible (siete dígitos)."""
    field = field_of(page, LOG_NUMBER_FIELD_ID)
    return bool(field and field.value
                and _LOG_NUMBER_RE.fullmatch(field.value.strip()))


def has_matricula(page: PageResult) -> bool:
    """La matrícula quedó confirmada en su formato canónico."""
    field = field_of(page, MATRICULA_FIELD_ID)
    return bool(field and field.value
                and _MATRICULA_RE.fullmatch(field.value.strip()))


_COMPONENT_RE = {
    "day": re.compile(r"^(?:0?[1-9]|[12]\d|3[01])$"),
    "month": _MONTH_RE,
    "year": _YEAR_RE,
}


def _usable_component(field_id: str, field: FieldResult) -> bool:
    pattern = _COMPONENT_RE.get(field_id)
    value = (field.value or "").strip().upper()
    return bool(value and pattern is not None and pattern.fullmatch(value))


def has_date(page: PageResult) -> bool:
    """Hay fecha utilizable: la combinada, o sus componentes legibles.

    Solo se exigen los componentes que la plantilla define. Con día, mes y
    año resueltos la fecha está, aunque la combinación no llegara a
    escribirse en ``page.date``.
    """
    if page.date and _DATE_RE.fullmatch(page.date):
        return True
    present = [
        (field_id, field_of(page, field_id))
        for field_id in DATE_FIELD_IDS
    ]
    present = [(field_id, field) for field_id, field in present if field]
    if not present:
        return False
    return all(
        _usable_component(field_id, field) for field_id, field in present
    )


def _index_evidence(page: PageResult) -> List[bool]:
    """Qué datos del índice salieron, entre los que la plantilla define.

    Una plantilla que no tiene matrícula no puede reprocharle a la página
    que le falte: solo se miran los campos que la página trae.
    """
    evidence: List[bool] = []
    if field_of(page, LOG_NUMBER_FIELD_ID) is not None:
        evidence.append(has_log_number(page))
    if field_of(page, MATRICULA_FIELD_ID) is not None:
        evidence.append(has_matricula(page))
    if any(field_of(page, field_id) for field_id in DATE_FIELD_IDS):
        evidence.append(has_date(page))
    return evidence


def page_status(page: PageResult) -> Status:
    """Estado de la página según lo que se pudo indexar de ella."""
    if page.blank:
        return Status.ERROR
    if not page.fields:
        return page.status

    evidence = _index_evidence(page)
    if evidence and not any(evidence):
        return Status.ERROR

    worst = max(
        (field.status for field in decisive_fields(page)),
        key=_ORDER.get,
        default=Status.OK,
    )
    if not all(evidence):
        return Status.WARNING
    # Un dato del índice marcado ya no puede dejar la página en ERROR: los
    # tres salieron, y lo que quedó marcado es una lectura por confirmar.
    return Status.WARNING if worst is not Status.OK else Status.OK


def ready_for_auto_index(page: PageResult) -> bool:
    """La página tiene todo lo necesario para indexarse automáticamente.

    No vuelve a interpretar los valores ni elimina inferencias. Un valor
    deducido por el libro sigue siendo válido cuando lo respaldan al menos
    dos lecturas independientes. Lo que se rechaza es un dato crítico marcado
    por las reglas normales o una inferencia sostenida por cero o una sola
    página. Una alineación dudosa no bloquea por sí sola: si aun así
    matrícula y ``log_number`` quedaron firmes, conserva el flujo automático.

    El número de bitácora debe conservar sus siete dígitos. La fecha puede
    resolverse después con las anclas del libro completo; la barrera previa a
    Quick Upload comprueba que esa inferencia sí haya producido un valor. Un
    log inválido va a ``REVISAR`` desde la exportación.
    """
    if page.blank:
        return False

    field = field_of(page, MATRICULA_FIELD_ID)
    if field is None or not has_matricula(page):
        return False
    if field.status is not Status.OK:
        return False
    if (
        field.source in _INFERRED_MATRICULA_SOURCES
        and (field.votes is None or field.votes < AUTO_INDEX_MIN_VOTES)
    ):
        return False
    if field.votes is not None and field.votes < AUTO_INDEX_MIN_VOTES:
        return False
    log_field = field_of(page, LOG_NUMBER_FIELD_ID)
    return bool(
        log_field
        and log_field.status is Status.OK
        and has_log_number(page)
    )


def recompute_page_status(page: PageResult) -> None:
    """Reescribe ``page.status`` con la política de indexación."""
    if not page.fields:
        return
    page.status = page_status(page)

"""Agrupación de páginas en "libros" (un avión por libro, 50 páginas).

Cada libro físico contiene 50 páginas de bitácora. El ``log_number``
(7 dígitos) termina en el "logpage": los últimos 2 dígitos indican la
página del libro (00-49 o 50-99). Dos páginas pertenecen al mismo libro
cuando comparten los primeros 5 dígitos del ``log_number`` (la serie del
libro) y los últimos 2 dígitos caen en la misma mitad (00-49 o 50-99).

El logpage está impreso a máquina, así que casi siempre es legible; solo
una página rota (número incompleto) deja la página sin ``log_number``.
Esas páginas se asignan al libro en curso.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from app.models.schemas import PageResult, ValidationReport

LOG_NUMBER_RE = re.compile(r"^\d{7}$")

# Mitad del rango de 50 páginas: logpage 00-49 o 50-99.
def _half(logpage: int) -> str:
    return "A" if logpage < 50 else "B"


def book_key(page: PageResult) -> Optional[Tuple[str, str]]:
    """Clave de libro (serie, mitad) a partir del log_number de la página.

    Returns:
        Tupla (primeros 5 dígitos, mitad "A"|"B"), o None si el log_number
        no es legible (página rota).
    """
    for field in page.fields:
        if field.field_id == "log_number" and field.value:
            value = LOG_NUMBER_RE.match(field.value.strip())
            if value:
                digits = value.group(0)
                return digits[:5], _half(int(digits[-2:]))
    return None


def log_number(page: PageResult) -> Optional[int]:
    """Número de bitácora (int) de la página, o None si no es legible."""
    for field in page.fields:
        if field.field_id == "log_number" and field.value:
            value = LOG_NUMBER_RE.match(field.value.strip())
            if value:
                return int(value.group(0))
    return None


def group_books(reports: List[ValidationReport]) -> List[List[PageResult]]:
    """Agrupa las páginas de todos los reportes en libros.

    Un libro es un bloque de páginas con la misma clave (serie, mitad).
    Cuando la clave cambia se cierra el libro en curso. Las páginas sin
    ``log_number`` legible se asignan al libro en curso; si no hay libro
    en curso todavía, se acumulan y se adhieren al primer libro que
    comience.
    """
    books: List[List[PageResult]] = []
    current: List[PageResult] = []
    current_key: Optional[Tuple[str, str]] = None
    pending: List[PageResult] = []

    def close() -> None:
        nonlocal current, current_key
        if current or pending:
            books.append(pending + current)
        current = []
        current_key = None
        pending.clear()

    for report in reports:
        for page in report.pages:
            key = book_key(page)
            if key is None:
                if current_key is None:
                    pending.append(page)
                else:
                    current.append(page)
                continue
            if key != current_key:
                close()
                current_key = key
            current.append(page)
    close()
    return books

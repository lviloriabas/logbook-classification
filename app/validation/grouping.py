"""Agrupación de páginas en libros usando el número de bitácora.

Cada libro físico contiene 50 páginas de bitácora. El ``log_number``
(7 dígitos) termina en el "logpage": los últimos 2 dígitos indican la
página del libro (00-49 o 50-99). Dos páginas pertenecen al mismo libro
cuando comparten los primeros 5 dígitos del ``log_number`` (la serie del
libro) y los últimos 2 dígitos caen en la misma mitad (00-49 o 50-99).

La agrupación se hace sobre todas las páginas de todos los PDFs y después
se ordena por ``log_number``. El orden físico del PDF no es una señal de
secuencia. Las páginas sin número legible se conservan, pero no pueden
participar en inferencias que dependan de la posición.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

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
    """Agrupa y ordena las páginas por clave de libro.

    A diferencia de la implementación anterior, una clave no se cierra al
    cambiar de PDF o al aparecer otra clave en medio. Esto evita dividir un
    libro cuando sus páginas están repartidas entre archivos o llegan fuera
    de orden.

    Las páginas sin ``log_number`` se agregan al único libro conocido cuando
    solo existe uno. Si hay varios libros posibles, se mantienen en un grupo
    separado para no asignarlas arbitrariamente a partir del orden del PDF.
    """
    grouped: Dict[Tuple[str, str], List[PageResult]] = defaultdict(list)
    unknown: List[PageResult] = []

    for report in reports:
        for page in report.pages:
            key = book_key(page)
            if key is None:
                unknown.append(page)
            else:
                grouped[key].append(page)

    books: List[List[PageResult]] = []
    for key in sorted(grouped):
        pages = grouped[key]
        pages.sort(key=lambda page: (
            log_number(page) if log_number(page) is not None else 1 << 30,
            page.page_number,
        ))
        books.append(pages)

    if unknown:
        if len(books) == 1:
            books[0].extend(unknown)
        else:
            # No se inventa una pertenencia ni un orden para estas páginas.
            books.append(list(unknown))

    return books

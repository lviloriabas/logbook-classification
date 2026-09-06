"""Periodo habitual de las bitácoras: mes actual y anterior, hasta hoy.

Las fechas antiguas son posibles pero requieren revisión. Ninguna fecha
manuscrita puede ser futura; la representación a fin de mes se comprueba
por mes porque su día se genera para el reporte, no se lee de la página.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

# Debajo de este año no hay bitácoras que indexar.
MIN_YEAR = 2000


def reference_date(today: Optional[date] = None) -> date:
    """Día contra el que se mide la ventana (hoy, salvo que se indique)."""
    return today if today is not None else date.today()


def year_is_possible(year: int, today: Optional[date] = None) -> bool:
    """Año de cuatro dígitos que una bitácora puede llevar escrito."""
    return MIN_YEAR <= year <= reference_date(today).year


def month_is_possible(
    year: int, month: int, today: Optional[date] = None
) -> bool:
    """Mes completo que una bitácora puede llevar escrito.

    Se compara por mes y no por día para no chocar con la política de fin
    de mes del CSV, que escribe el último día del mes en curso y por tanto
    una fecha unos días por delante de hoy.
    """
    reference = reference_date(today)
    return 1 <= month <= 12 and year_is_possible(year, today) and (
        (year, month) <= (reference.year, reference.month)
    )


def date_is_possible(value: date, today: Optional[date] = None) -> bool:
    """Fecha que una página pudo llevar escrita cuando se escaneó."""
    return year_is_possible(value.year, today) and value <= reference_date(today)


def usual_start(today: Optional[date] = None) -> date:
    """Primer día del mes anterior, incluyendo diciembre al pasar a enero."""
    first = reference_date(today).replace(day=1)
    return (first - timedelta(days=1)).replace(day=1)


def days_outside_usual(value: date, today: Optional[date] = None) -> int:
    """Días que separan la fecha de la ventana habitual (0 si cae dentro).

    Una fecha adelantada devuelve los días que le sobran; una atrasada,
    los que le faltan para entrar en la ventana. Sirve para ordenar
    candidatos, no para descartarlos: un libro antiguo es raro, no
    imposible.
    """
    reference = reference_date(today)
    if value > reference:
        return (value - reference).days
    return max(0, (usual_start(reference) - value).days)


def years_outside_usual(value: date, today: Optional[date] = None) -> int:
    """Anos enteros que separan la fecha de la ventana habitual.

    Es la misma distancia medida en grueso. Sirve para comparar
    candidatos sin que el borde de la ventana decida nada: un libro viejo
    entero puntua igual en todas sus paginas, y solo destaca la pagina
    que un ano mal leido manda decadas fuera.
    """
    return days_outside_usual(value, today) // 365


def is_usual(value: date, today: Optional[date] = None) -> bool:
    """Indica si la fecha cae en la ventana habitual de una ejecución."""
    return days_outside_usual(value, today) == 0

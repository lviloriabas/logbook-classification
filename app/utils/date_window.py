"""Dos periodos de las bitácoras, con usos distintos.

El **habitual** (mes actual y anterior, hasta hoy) ordena candidatos: de dos
lecturas posibles de una misma fecha, la que cae en él es la creíble. Es
estrecho a propósito, porque solo sirve para comparar.

El de **revisión** es mucho más ancho y decide otra cosa: cuándo una fecha
es tan antigua que tiene que mirarla una persona. Una entrega normal arrastra
semanas o unos meses de cola de escaneo, y medir eso con el periodo habitual
mandaba a REVISAR bitácoras bien leídas solo por llevar un mes de más.

Ninguna fecha manuscrita puede ser futura; la representación a fin de mes se
comprueba por mes porque su día se genera para el reporte, no se lee de la
página.
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


# Meses que abarca el periodo de revisión, contando el de la ejecución. Un
# atraso de semanas o de unos meses es lo normal en una entrega y no dice
# nada de la lectura. Lo que casi nunca es real es un año entero de
# diferencia: ahí el año suele estar mal leído (un '26' que salió '25' o
# '24'). Por eso el periodo se cierra justo antes de cumplirse el año, y una
# fecha del mismo mes del año pasado sí pasa a REVISAR.
REVIEW_MONTHS = 12


def review_start(today: Optional[date] = None) -> date:
    """Primer día del periodo que no necesita revisión por antigüedad."""
    reference = reference_date(today)
    months = reference.year * 12 + reference.month - REVIEW_MONTHS
    return date(months // 12, months % 12 + 1, 1)


def needs_review_for_age(value: date, today: Optional[date] = None) -> bool:
    """Indica si la fecha es tan antigua que la página debe revisarse."""
    return value < review_start(today)


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

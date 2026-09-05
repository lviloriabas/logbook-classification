"""Ventana temporal en la que puede caer la fecha de una bitácora.

Lo que se indexa es reciente: casi todo entra dentro del mes siguiente a
la última página escrita, y la mayoría de las páginas caen alrededor del
día en que se procesan. De vez en cuando llega un libro de meses atrás y
muy rara vez uno antiguo, así que una fecha vieja no se descarta: se
acepta y se anota como poco habitual. Lo que no existe es una fecha
adelantada: ninguna página se firma después del día en que se escanea, de
modo que un año posterior al de la ejecución es una lectura equivocada y
no una bitácora del futuro.

La comparación con el futuro se hace por año y no por día a propósito. El
año es la parte que el OCR confunde de verdad (un '26' leído '96' o '28'
manda la fecha décadas fuera), y a resolución de año un reloj mal puesto
por unos días no borra fechas correctas.

Nada de esto inventa fechas ni las acerca a hoy: la ventana solo sirve
para descartar una lectura imposible antes de que se propague al resto del
libro y para ordenar entre lecturas que el OCR ya propuso.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

# Debajo de este año no hay bitácoras que indexar.
MIN_YEAR = 2000
# Ventana habitual hacia atrás: casi todo lo que se procesa cae aquí.
USUAL_DAYS = 45


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
    return year_is_possible(year, today) and (
        (year, month) <= (reference.year, reference.month)
    )


def date_is_possible(value: date, today: Optional[date] = None) -> bool:
    """Fecha que una página pudo llevar escrita cuando se escaneó."""
    return year_is_possible(value.year, today)


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
    return max(0, (reference - value).days - USUAL_DAYS)


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

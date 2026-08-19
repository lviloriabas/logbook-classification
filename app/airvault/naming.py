"""Nombre del lote en AirVault.

El nombre es lo unico que el sistema y AirVault comparten para reconocer un
lote, asi que tiene que ser unico y reconstruible. Se arma con un prefijo
fijo y la marca de tiempo de la corrida, en el mismo formato que ya usa el
nombre del CSV (``BITS 18 AUG 2026 05 42``), de modo que el lote y la
carpeta de la corrida se puedan cruzar de un vistazo.

    DP | BIT 18 AUG 2026 05 42

Hace falta que sea unico: hoy conviven en la cola dos lotes llamados
``DP | BIT Mix | Viernes 14 AUG`` y dos ``DP | BIT Mix 5 | Viernes 14 AUG``,
y con nombres repetidos no hay forma de saber en cual escribir.
"""

from __future__ import annotations

import html
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

PREFIJO_POR_DEFECTO = "DP | BIT"

_MESES = (
    "JAN", "FEB", "MAR", "APR", "MAY", "JUN",
    "JUL", "AUG", "SEP", "OCT", "NOV", "DEC",
)

# ``BITS 18 AUG 2026 05 42`` o ``DP | BIT 18 AUG 2026 05 42``.
_MARCA_RE = re.compile(
    r"(\d{1,2})\s+(" + "|".join(_MESES) + r")\s+(\d{4})\s+(\d{2})\s+(\d{2})",
    re.IGNORECASE,
)


def marca_de_tiempo(momento: Optional[datetime] = None) -> str:
    """Marca ``DD MON YYYY HH MM``, igual que el nombre del CSV de corrida."""
    momento = momento or datetime.now()
    return (f"{momento.day:02d} {_MESES[momento.month - 1]} "
            f"{momento.year:04d} {momento.hour:02d} {momento.minute:02d}")


def nombre_de_lote(prefijo: str = PREFIJO_POR_DEFECTO,
                   momento: Optional[datetime] = None) -> str:
    """Nombre completo del lote: prefijo mas marca de tiempo."""
    return f"{prefijo.strip()} {marca_de_tiempo(momento)}".strip()


def marca_de_corrida(ruta: Path | str) -> Optional[str]:
    """Saca la marca de tiempo del nombre de un CSV o carpeta de corrida.

    Sirve para que el lote se llame igual que la corrida que lo produjo, en
    vez de con la hora en que alguien se acordo de subirlo.
    """
    texto = str(ruta)
    coincidencia = _MARCA_RE.search(texto)
    if not coincidencia:
        return None
    dia, mes, anio, hora, minuto = coincidencia.groups()
    return f"{int(dia):02d} {mes.upper()} {anio} {hora} {minuto}"


def nombre_desde_corrida(
    ruta: Path | str, prefijo: str = PREFIJO_POR_DEFECTO,
    momento: Optional[datetime] = None,
) -> str:
    """Nombre del lote a partir de la ruta de la corrida.

    Si la ruta no trae marca reconocible se usa la hora actual, que es lo
    unico que queda, pero el nombre sigue siendo unico.
    """
    marca = marca_de_corrida(ruta)
    if marca:
        return f"{prefijo.strip()} {marca}".strip()
    return nombre_de_lote(prefijo, momento)


def limpiar_nombre_remoto(nombre: str) -> str:
    """Deshace el escapado HTML con el que AirVault devuelve los nombres.

    El listado entrega ``DP | Bit&#225;coras varias 4``; sin deshacerlo,
    cualquier comparacion con acentos falla.
    """
    return html.unescape(str(nombre or "")).strip()


def prefijo_de_busqueda(nombre: str) -> str:
    """Trozo que conviene mandar como filtro al servidor.

    El filtro de AirVault es una coincidencia de subcadena sin distinguir
    mayusculas, asi que se manda el nombre completo cuando existe: reduce el
    listado a los pocos candidatos reales antes de comparar en local.
    """
    limpio = str(nombre or "").strip()
    return limpio or PREFIJO_POR_DEFECTO

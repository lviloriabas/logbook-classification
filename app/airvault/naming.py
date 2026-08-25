"""Nombre del batch en AirVault.

El nombre es lo unico que el sistema y AirVault comparten para reconocer un
batch, asi que tiene que ser unico y reconstruible. Se arma con un prefijo
fijo y la marca de tiempo de la ejecución, en el mismo formato que ya usa el
nombre del CSV (``BITS 18 AUG 2026 05 42``), de modo que el batch y la
carpeta de la ejecución se puedan cruzar de un vistazo.

    DP | BITS 18 AUG 2026 05 42

La marca es la del **procesamiento**, no la de la subida: el batch se llama
igual que la ejecución que lo produjo y los dos se cruzan de un vistazo. Todo
el nombre va en mayusculas, como los batches que ya hay en la cola.

Hace falta que sea unico: hoy conviven en la cola dos batches llamados
``DP | BIT Mix | Viernes 14 AUG`` y dos ``DP | BIT Mix 5 | Viernes 14 AUG``,
y con nombres repetidos no hay forma de saber en cual escribir.
"""

from __future__ import annotations

import html
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

PREFIJO_POR_DEFECTO = "DP | BITS"

# Marca del batch que recoge las bitacoras cuya matricula requiere revision.
# No se indexa: se sube para que alguien la resuelva a mano en el Web Index.
ETIQUETA_REVISAR = "REVISAR"

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
    """Marca ``DD MON YYYY HH MM``, igual que el nombre del CSV de ejecución."""
    momento = momento or datetime.now()
    return (f"{momento.day:02d} {_MESES[momento.month - 1]} "
            f"{momento.year:04d} {momento.hour:02d} {momento.minute:02d}")


def en_mayusculas(nombre: str) -> str:
    """Deja el nombre como se escriben los batches en AirVault.

    No es cosmetico: el filtro del servidor no distingue mayusculas, pero la
    cola si las muestra, y un batch escrito distinto de los demas se lee como
    de otra procedencia.
    """
    return " ".join(str(nombre or "").upper().split())


def nombre_de_lote(prefijo: str = PREFIJO_POR_DEFECTO,
                   momento: Optional[datetime] = None) -> str:
    """Nombre completo del batch: prefijo mas marca de tiempo."""
    return en_mayusculas(f"{prefijo.strip()} {marca_de_tiempo(momento)}")


def marca_de_corrida(ruta: Path | str) -> Optional[str]:
    """Saca la marca de tiempo del nombre de un CSV o carpeta de ejecución.

    Sirve para que el batch se llame igual que la ejecución que lo produjo, en
    vez de con la hora en que alguien se acordo de subirlo.
    """
    texto = str(ruta)
    coincidencia = _MARCA_RE.search(texto)
    if not coincidencia:
        return None
    dia, mes, anio, hora, minuto = coincidencia.groups()
    return f"{int(dia):02d} {mes.upper()} {anio} {hora} {minuto}"


def momento_de_procesamiento(ruta: Path | str) -> Optional[datetime]:
    """Cuando se proceso la ejecución, segun sus propios archivos.

    Es el respaldo para las ejecuciones cuya carpeta no lleva la marca en el
    nombre. Vale mucho mas que la hora actual: el batch tiene que decir
    cuando se proceso la bitacora, no cuando alguien se acordo de subirla.
    """
    archivo = Path(ruta)
    for candidato in (archivo, archivo.parent, archivo.parent.parent):
        try:
            if candidato.exists():
                return datetime.fromtimestamp(candidato.stat().st_mtime)
        except OSError:
            continue
    return None


def nombre_desde_corrida(
    ruta: Path | str, prefijo: str = PREFIJO_POR_DEFECTO,
    momento: Optional[datetime] = None,
) -> str:
    """Nombre del batch a partir de la ruta de la ejecución.

    La marca sale del nombre de la carpeta de la ejecución, que es la hora en
    que se proceso. Si la carpeta no la trae, se toma la del archivo, que
    sigue siendo la del procesamiento; la hora actual es el ultimo recurso y
    solo aparece cuando no hay ni archivo que mirar.
    """
    marca = marca_de_corrida(ruta)
    if marca:
        return en_mayusculas(f"{prefijo.strip()} {marca}")
    return nombre_de_lote(prefijo, momento or momento_de_procesamiento(ruta))


def nombre_de_parte(base: str, indice: int, total: int) -> str:
    """Nombre del batch de una parte: ``<base> -2``.

    Con una sola parte devuelve el nombre a secas, que es el de siempre.
    El sufijo hace falta porque cada parte es un batch distinto y los batches
    se localizan por nombre: dos con el mismo no habria forma de separarlos.
    """
    if total <= 1:
        return en_mayusculas(base)
    return en_mayusculas(f"{base} -{indice}")


def nombre_de_revisar(base: str) -> str:
    """Nombre del batch que recoge las bitacoras para revision manual.

    Va aparte y no se indexa. Mezcladas con las demas quedarian bloqueadas
    en medio de un batch de cuatrocientas paginas, donde nadie las encuentra;
    en su propio batch, marcado, se ven en la cola y se resuelven a mano.
    """
    return en_mayusculas(f"{base} {ETIQUETA_REVISAR}")


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

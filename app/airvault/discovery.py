"""Localizar en AirVault el lote que corresponde a un trabajo.

El usuario sube el lote y el sistema lo tiene que encontrar solo. El nombre
es lo unico que los dos lados comparten, asi que la busqueda es por nombre,
con dos precauciones: los lotes creados por Quick Upload llegan como
``<nombre> - <usuario>``, y puede haber mas de un lote con nombres
parecidos, en cuyo caso no se adivina.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence

from loguru import logger

from app.airvault.client import ResumenLote
from app.airvault.naming import limpiar_nombre_remoto

_SEPARADORES = re.compile(r"[\s_|\-]+")


def normalizar_nombre(nombre: str) -> str:
    """Normaliza para comparar: sin mayusculas, sin separadores repetidos.

    Se deshace primero el escapado HTML porque el listado devuelve los
    nombres con entidades (``Bit&#225;coras``) y de otro modo cualquier
    nombre con acento no coincidiria nunca.
    """
    limpio = limpiar_nombre_remoto(nombre).lower()
    limpio = _SEPARADORES.sub(" ", limpio)
    return limpio.strip()


def _nombre_base(nombre: str) -> str:
    """Quita el sufijo ``- usuario@dominio`` que agrega Quick Upload."""
    partes = str(nombre or "").rsplit(" - ", 1)
    if len(partes) == 2 and "@" in partes[1]:
        return partes[0]
    return nombre


class LoteNoEncontrado(RuntimeError):
    """Ningun lote coincide con el nombre buscado."""


class LoteAmbiguo(RuntimeError):
    """Mas de un lote coincide y no se puede elegir sin preguntar."""


@dataclass(frozen=True)
class Coincidencia:
    lote: ResumenLote
    exacta: bool


def buscar(
    lotes: Sequence[ResumenLote], nombre: str, repo_id: int | None = None,
    paginas_esperadas: int | None = None,
) -> ResumenLote:
    """Elige el lote que corresponde al nombre pedido.

    Prioriza la coincidencia exacta de nombre. Si no la hay, acepta la que
    coincide tras quitar el sufijo de Quick Upload. Cuando quedan varias
    candidatas se usa la cantidad de paginas para desempatar, y si aun asi
    hay mas de una se levanta :class:`LoteAmbiguo`: escribir en el lote
    equivocado es peor que pedirle al usuario que lo diga.
    """
    objetivo = normalizar_nombre(nombre)
    if not objetivo:
        raise LoteNoEncontrado("Hay que decir el nombre del lote")

    candidatas: List[Coincidencia] = []
    for lote in lotes:
        if repo_id is not None and lote.repo_id and lote.repo_id != repo_id:
            continue
        propio = normalizar_nombre(lote.nombre)
        if propio == objetivo:
            candidatas.append(Coincidencia(lote, True))
        elif normalizar_nombre(_nombre_base(lote.nombre)) == objetivo:
            candidatas.append(Coincidencia(lote, False))

    if not candidatas:
        raise LoteNoEncontrado(
            f"No hay ningun lote llamado {nombre!r} en AirVault"
        )

    exactas = [c for c in candidatas if c.exacta]
    elegibles = exactas or candidatas
    if len(elegibles) > 1 and paginas_esperadas is not None:
        por_paginas = [
            c for c in elegibles if c.lote.paginas == paginas_esperadas
        ]
        if por_paginas:
            elegibles = por_paginas
    if len(elegibles) > 1:
        nombres = ", ".join(
            f"{c.lote.batch_id} ({c.lote.paginas} pags)" for c in elegibles
        )
        raise LoteAmbiguo(
            f"Hay {len(elegibles)} lotes que coinciden con {nombre!r}: "
            f"{nombres}. Indicar el batch id a mano."
        )
    return elegibles[0].lote


def esperar(
    listar: Callable[..., Sequence[ResumenLote]],
    nombre: str,
    repo_id: int | None = None,
    paginas_esperadas: int | None = None,
    espera_s: float = 20.0,
    limite_s: float = 900.0,
    dormir: Callable[[float], None] = time.sleep,
    reloj: Callable[[], float] = time.monotonic,
) -> ResumenLote:
    """Sondea el listado hasta que el lote aparezca o se agote el tiempo.

    Un lote recien subido tarda en pasar por el procesamiento del servidor,
    asi que la ausencia no es un error hasta que vence el limite. El
    ``LoteAmbiguo`` si corta de inmediato: esperar no lo va a resolver.
    """
    inicio = reloj()
    intento = 0
    while True:
        intento += 1
        try:
            return buscar(_listar(listar, nombre), nombre, repo_id,
                          paginas_esperadas)
        except LoteNoEncontrado:
            transcurrido = reloj() - inicio
            if transcurrido >= limite_s:
                raise
            logger.info(
                "El lote {!r} todavia no aparece ({:.0f}s de {:.0f}s), "
                "reintento {}", nombre, transcurrido, limite_s, intento,
            )
            dormir(espera_s)


def _listar(listar: Callable[..., Sequence[ResumenLote]],
            nombre: str) -> Sequence[ResumenLote]:
    """Pide el listado filtrado si el cliente lo admite.

    Filtrar del lado del servidor evita traerse la cola entera en cada
    sondeo, pero el cliente falso de los tests no recibe argumentos, asi
    que se cae al listado completo sin ruido.
    """
    try:
        return listar(nombre)
    except TypeError:
        return listar()


def buscar_por_id(
    lotes: Sequence[ResumenLote], batch_id: str
) -> Optional[ResumenLote]:
    objetivo = str(batch_id).strip().upper()
    for lote in lotes:
        if lote.batch_id.strip().upper() == objetivo:
            return lote
    return None

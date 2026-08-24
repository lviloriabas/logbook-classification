"""Localizar en AirVault el batch que corresponde a un trabajo.

El usuario sube el batch y el sistema lo tiene que encontrar solo. Usa varias
señales y ninguna cantidad se usa por sí sola para adivinar:

1. **Nombre visible**, incluido ``<nombre> - <usuario>``.
2. **Cantidad exacta de páginas**, que es un requisito y no un desempate
   suficiente.
3. **Contenido**, mediante Batch Name interno y una muestra distribuida de
   Log Page Number. Este contraste también se aplica cuando el nombre visible
   ya parece correcto; si AirVault pierde o trunca el nombre, permite corregir
   el mismo ID sin volver a subir el PDF.
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


def _detalle_candidatos(lotes: Sequence[ResumenLote]) -> str:
    return "; ".join(
        f"ID {lote.batch_id}, «{lote.nombre}», {lote.paginas} páginas"
        for lote in lotes
    )


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


def recien_llegados(
    lotes: Sequence[ResumenLote],
    previos: Sequence[str],
    repo_id: int | None = None,
) -> List[ResumenLote]:
    """Batches que no estaban en la cola antes de subir."""
    conocidos = {str(b).strip().upper() for b in previos or ()}
    return [
        lote
        for lote in lotes
        if lote.batch_id.strip().upper() not in conocidos
        and (repo_id is None or not lote.repo_id or lote.repo_id == repo_id)
    ]


def buscar_nuevo(
    lotes: Sequence[ResumenLote],
    previos: Sequence[str],
    repo_id: int | None = None,
    paginas_esperadas: int | None = None,
) -> Optional[ResumenLote]:
    """El batch que aparecio despues de subir, cuando el nombre no sirve.

    Quick Upload envia el nombre, pero AirVault puede perderlo y mostrar
    ``Empty-Batch``. La diferencia con la cola previa identifica candidatos;
    quien llama debe confirmar despues el contenido antes de renombrar.

    Devuelve ``None`` mientras todavía no ha aparecido nada, que no es un
    fallo: el servidor tarda en procesar lo subido. Si aparecio mas de uno
    se desempata por cantidad de paginas, y si aun asi queda mas de uno se
    levanta :class:`LoteAmbiguo` en vez de escribir en el equivocado.
    """
    nuevos = recien_llegados(lotes, previos, repo_id)
    if not nuevos:
        return None
    if len(nuevos) > 1 and paginas_esperadas is not None:
        por_paginas = [l for l in nuevos if l.paginas == paginas_esperadas]
        if por_paginas:
            nuevos = por_paginas
    if len(nuevos) > 1:
        raise LoteAmbiguo(
            f"AirVault mostró {len(nuevos)} batches nuevos que podrían ser "
            f"esta carga: {_detalle_candidatos(nuevos)}. No se eligió ni "
            "se indexó ninguno para evitar escribir en el batch equivocado. "
            "El programa leerá el Batch Name guardado dentro de cada archivo, "
            "elegirá el ID correspondiente, lo renombrará y confirmará el "
            "cambio automáticamente."
        )
    return nuevos[0]


class LoteNoEncontrado(RuntimeError):
    """Ningun batch coincide con el nombre buscado."""


class LoteAmbiguo(RuntimeError):
    """Mas de un batch coincide y no se puede elegir sin preguntar."""


@dataclass(frozen=True)
class Coincidencia:
    lote: ResumenLote
    exacta: bool


def buscar(
    lotes: Sequence[ResumenLote],
    nombre: str,
    repo_id: int | None = None,
    paginas_esperadas: int | None = None,
) -> ResumenLote:
    """Elige el batch que corresponde al nombre pedido.

    Prioriza la coincidencia exacta de nombre. Si no la hay, acepta la que
    coincide tras quitar el sufijo de Quick Upload. Cuando quedan varias
    candidatas se usa la cantidad de paginas para desempatar, y si aun asi
    hay mas de una se levanta :class:`LoteAmbiguo`: escribir en el batch
    equivocado es peor que pedirle al usuario que lo diga.
    """
    objetivo = normalizar_nombre(nombre)
    if not objetivo:
        raise LoteNoEncontrado("Hay que decir el nombre del batch")

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
        raise LoteNoEncontrado(f"No hay ningun batch llamado {nombre!r} en AirVault")

    exactas = [c for c in candidatas if c.exacta]
    elegibles = exactas or candidatas
    if len(elegibles) > 1 and paginas_esperadas is not None:
        por_paginas = [c for c in elegibles if c.lote.paginas == paginas_esperadas]
        if por_paginas:
            elegibles = por_paginas
    if len(elegibles) > 1:
        lotes_elegibles = [c.lote for c in elegibles]
        raise LoteAmbiguo(
            f"AirVault encontró {len(lotes_elegibles)} batches que coinciden "
            f"con «{nombre}»: {_detalle_candidatos(lotes_elegibles)}. No se "
            "eligió ni se indexó ninguno para evitar escribir en el batch "
            "equivocado. Confirme los IDs y las páginas en Web Index, deje un "
            "solo batch correcto con ese título y vuelva a pulsar «Revisar en "
            "AirVault». Elimine allí únicamente el duplicado que ya haya "
            "confirmado."
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
    previos: Optional[Sequence[str]] = None,
) -> ResumenLote:
    """Sondea el listado hasta que el batch aparezca o se agote el tiempo.

    Un batch recien subido tarda en pasar por el procesamiento del servidor,
    asi que la ausencia no es un error hasta que vence el limite. El
    ``LoteAmbiguo`` si corta de inmediato: esperar no lo va a resolver.
    """
    inicio = reloj()
    intento = 0
    while True:
        intento += 1
        try:
            return buscar(_listar(listar, nombre), nombre, repo_id, paginas_esperadas)
        except LoteNoEncontrado:
            if previos is not None:
                nuevo = buscar_nuevo(
                    _listar(listar, ""), previos, repo_id, paginas_esperadas
                )
                if nuevo is not None:
                    logger.info(
                        "El batch llego a la cola como {!r}; se reconoce "
                        "porque no estaba antes de subir: {}",
                        nuevo.nombre,
                        nuevo.batch_id,
                    )
                    return nuevo
            transcurrido = reloj() - inicio
            if transcurrido >= limite_s:
                raise
            logger.info(
                "El batch {!r} todavía no aparece ({:.0f}s de {:.0f}s), reintento {}",
                nombre,
                transcurrido,
                limite_s,
                intento,
            )
            dormir(espera_s)


def _listar(
    listar: Callable[..., Sequence[ResumenLote]], nombre: str
) -> Sequence[ResumenLote]:
    """Pide el listado filtrado si el cliente lo admite.

    Filtrar del lado del servidor evita traerse la cola entera en cada
    sondeo, pero el cliente falso de los tests no recibe argumentos, asi
    que se cae al listado completo sin ruido.
    """
    try:
        return listar(nombre)
    except TypeError:
        return listar()


def buscar_por_id(lotes: Sequence[ResumenLote], batch_id: str) -> Optional[ResumenLote]:
    objetivo = str(batch_id).strip().upper()
    for lote in lotes:
        if lote.batch_id.strip().upper() == objetivo:
            return lote
    return None

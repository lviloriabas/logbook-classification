"""Buscar una bitácora entre los batches que están en la cola.

La lista de bitácoras de un batch ya se podía buscar, pero solo dentro de
ese batch: para saber por dónde viajó una bitácora había que abrir uno por
uno los batches de la cola. Aquí se busca en todos a la vez y se responde
la pregunta que se hace de verdad: en qué batch está.

La respuesta puede ser más de uno, y no es raro. Una ejecución repartida
en partes manda las bitácoras dudosas al batch REVISAR además de a su
parte; una ejecución que se subió dos veces deja la misma bitácora en el
batch de antes y en el nuevo; y la cola conserva los pendientes de
ejecuciones anteriores. Por eso lo que se devuelve es la lista de batches
donde está, con la página que ocupa en cada uno, y no un solo sitio.

La coincidencia exacta manda: si el texto es igual que el Log Page de una
bitácora, se contestan solo esas y no las que además lo llevan dentro de
otro dato. Buscar «2271042» pregunta por esa bitácora, no por el archivo
de origen que lo tenga en el nombre.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from app.airvault.mapping import fecha_airvault
from app.airvault.model import Registro

#: Batches que se nombran en la respuesta antes de resumir el resto.
_TOPE_BATCHES = 4
#: Páginas que se nombran de un mismo batch antes de resumir el resto.
_TOPE_PAGINAS = 6


@dataclass(frozen=True)
class Hallazgo:
    """Un batch de la cola que lleva dentro la bitácora buscada."""

    #: Posición del batch en la cola, que es su fila en la tabla.
    fila: int
    nombre: str
    #: Páginas del batch donde está, las mismas que enseña Web Index.
    paginas: tuple[int, ...]


def valores_de(registro: Registro) -> tuple[str, ...]:
    """Datos de la bitácora por los que se la puede buscar.

    Son los que identifican la página, no los que dicen dónde acabó: la
    página del batch es la respuesta de la búsqueda y buscar por ella
    devolvería la misma página de todos los batches. La fecha va en los
    dos formatos, el del CSV y el que se le escribe a AirVault, para que
    valga la que se tenga a mano.
    """
    origen = (
        f"{registro.archivo_origen}, p. {registro.pagina_origen}"
        if registro.archivo_origen else ""
    )
    return tuple(
        valor for valor in (
            registro.matricula,
            registro.log_number,
            registro.flight_number,
            registro.fecha,
            fecha_airvault(registro.fecha),
            registro.archivo_origen,
            origen,
        ) if valor
    )


def _pagina_de(registro: Registro) -> int:
    """Qué página del batch ocupa, que es la que hay que decir."""
    return int(registro.pagina_batch or registro.seq or 0)


def buscar_en_la_cola(
    batches: Sequence[tuple[str, Sequence[Registro]]], texto: str
) -> list[Hallazgo]:
    """Los batches de la cola que llevan la bitácora buscada.

    ``batches`` viene en el orden de la cola y cada uno es su nombre y sus
    páginas, separadoras incluidas: las divisorias se saltan aquí porque
    no son bitácoras y no hay nada que buscar en ellas.
    """
    buscado = str(texto or "").strip().casefold()
    if not buscado:
        return []
    exactos: list[Hallazgo] = []
    parciales: list[Hallazgo] = []
    for fila, (nombre, registros) in enumerate(batches):
        iguales: list[int] = []
        contienen: list[int] = []
        for registro in registros:
            if registro.es_separador:
                continue
            valores = [valor.casefold() for valor in valores_de(registro)]
            if buscado in valores:
                iguales.append(_pagina_de(registro))
            elif any(buscado in valor for valor in valores):
                contienen.append(_pagina_de(registro))
        if iguales:
            exactos.append(Hallazgo(fila, nombre, tuple(iguales)))
        if contienen:
            parciales.append(Hallazgo(fila, nombre, tuple(contienen)))
    return exactos or parciales


def _enumerar(partes: Sequence[str]) -> str:
    """«a», «a y b», «a, b y c»: como se lee una lista en voz alta."""
    if len(partes) <= 1:
        return "".join(partes)
    return f"{', '.join(partes[:-1])} y {partes[-1]}"


def _paginas_largo(paginas: Sequence[int]) -> str:
    """«página 12», «páginas 12, 13 y 40»: cuando el batch es uno solo."""
    nombradas = [str(pagina) for pagina in paginas[:_TOPE_PAGINAS]]
    resto = len(paginas) - len(nombradas)
    if resto:
        nombradas.append(f"otras {resto}")
    palabra = "página" if len(paginas) == 1 else "páginas"
    return f"{palabra} {_enumerar(nombradas)}"


def _paginas_corto(paginas: Sequence[int]) -> str:
    """Lo mismo abreviado, para la lista de varios batches."""
    nombradas = [str(pagina) for pagina in paginas[:_TOPE_PAGINAS]]
    resto = len(paginas) - len(nombradas)
    if resto:
        nombradas.append(f"otras {resto}")
    palabra = "p." if len(paginas) == 1 else "pp."
    return f"{palabra} {_enumerar(nombradas)}"


def frase_de(texto: str, hallazgos: Sequence[Hallazgo]) -> str:
    """Lo que se lee debajo de la tabla: en qué batches está la bitácora.

    Con varios se dice cuántos son antes de nombrarlos, porque esa es la
    parte que sorprende: la misma bitácora puede ir en una parte y en el
    batch REVISAR a la vez.
    """
    buscado = str(texto or "").strip()
    if not buscado:
        return ""
    if not hallazgos:
        return f"«{buscado}»: no está en ninguna bitácora de la cola."
    if len(hallazgos) == 1:
        solo = hallazgos[0]
        return (
            f"«{buscado}»: en «{solo.nombre}», "
            f"{_paginas_largo(solo.paginas)}."
        )
    nombrados = [
        f"«{hallazgo.nombre}» ({_paginas_corto(hallazgo.paginas)})"
        for hallazgo in hallazgos[:_TOPE_BATCHES]
    ]
    resto = len(hallazgos) - len(nombrados)
    if resto:
        nombrados.append(f"otros {resto}")
    return (
        f"«{buscado}»: en {len(hallazgos)} batches a la vez - "
        f"{_enumerar(nombrados)}."
    )

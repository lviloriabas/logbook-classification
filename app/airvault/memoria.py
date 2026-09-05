"""Lo que AirVault ya tiene indexado, puesto a comprobar la memoria local.

El programa recuerda entre ejecuciones la matricula de cada libro y los
extremos de fecha que confirmo. Esa memoria se aprende del propio OCR, asi
que se puede equivocar, y cuando se equivoca no hay nada que la corrija:
:mod:`app.validation.book_memory` lo explica entero. Este modulo es la
mitad que trae la evidencia de fuera.

Hay dos formas de preguntarle a AirVault, y las dos evitan cualquier
intervencion:

**Las paginas que el plan ya lee.** Antes de escribir nada, el indexado se
trae de AirVault las paginas del batch para contrastarlas. Las que estan en
verde traen el indice que la empresa da por bueno: lo escribio este
programa, o lo corrigio una persona en Web Index. No cuesta ni una peticion
de mas, va en cada plan y en cada dry run, y no hay que encender nada.

Solo cuentan las verdes. En cualquier otro estado lo que se ve en Aircraft
es la clasificacion inicial de Quick Upload, que pone en todas las paginas
del archivo el avion de la primera bitacora; contrastar contra eso acusaria
a media entrega (es la misma razon por la que
:func:`app.airvault.guards.verificar_alineacion` tampoco la mira).

**Web Search.** Alcanza a los libros que no vienen en el batch de hoy, que
son casi todos los de la memoria, y sobre todo a los que se publicaron
antes de que existiera este programa: ahi AirVault es de verdad una fuente
externa. Cuesta unas peticiones por libro y la consulta no esta documentada
(ver :mod:`app.airvault.websearch`), asi que vive en una comprobacion
aparte que se pide expresamente.

Queda la objecion evidente de la primera forma: si la memoria estaba mal y
este programa escribio esa matricula en AirVault, releerla parece darse la
razon solo. Es cierto que no la confirma, pero tampoco hace dano: cuando lo
leido coincide con lo guardado no se toca nada. Lo unico que cambia la
memoria es que AirVault diga algo **distinto**, y eso solo pasa cuando
alguien lo corrigio o cuando esas paginas se indexaron sin este programa.
Que son justo los dos casos en los que AirVault tiene razon y la memoria
no.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Sequence

from loguru import logger

from app.airvault.config import (
    CAMPO_END_DATE,
    CAMPO_LOG_NUMBER,
    CAMPO_MATRICULA,
    ESTADO_VALIDO,
)
from app.airvault.mapping import (
    fecha_desde_airvault,
    normalizar_log_number,
    normalizar_matricula,
)
from app.airvault.websearch import Buscador, muestra_de
from app.utils.fleet import FLEET_FILENAME, load_fleet
from app.validation.book_corrector import BOOK_MATRICULAS_FILENAME
from app.validation.book_memory import (
    Informe,
    Observacion,
    libros_guardados,
    verificar,
)
from app.validation.date_corrector import BOOK_DATES_FILENAME

# Cuantas bitacoras de cada libro se le preguntan a Web Search. Con una sola
# no se puede corregir nada (reemplazar una entrada pide dos), y de un libro
# publicado a medias las primeras paginas no dicen lo mismo que las ultimas,
# asi que se preguntan unas cuantas repartidas por el libro.
BITACORAS_POR_LIBRO = 4

# Paginas de un libro. La mitad A son las 00 a 49 y la B las 50 a 99.
PAGINAS_POR_LIBRO = 50


def _fecha(valor: object) -> Optional[date]:
    """La fecha de un campo de AirVault, o None si no se entiende."""
    iso = fecha_desde_airvault(valor)
    return date.fromisoformat(iso) if iso else None


def observaciones_de_paginas(paginas: Iterable[object]) -> List[Observacion]:
    """Lo que AirVault ya daba por bueno en las paginas que se leyeron.

    Recibe las :class:`app.airvault.client.PaginaIndexada` que el plan trajo
    del batch. Se queda con las que estan en verde y traen numero de
    bitacora: sin numero no se sabe de que libro hablan, y sin verde lo que
    se ve no es un indice sino la clasificacion del archivo entero.
    """
    vistas: Dict[str, Observacion] = {}
    for pagina in paginas:
        valores = getattr(pagina, "valores", None)
        if getattr(pagina, "estado", None) != ESTADO_VALIDO:
            continue
        if not isinstance(valores, Mapping):
            continue
        numero = normalizar_log_number(valores.get(CAMPO_LOG_NUMBER, ""))
        if not numero:
            continue
        matricula = normalizar_matricula(valores.get(CAMPO_MATRICULA, ""))
        fecha = _fecha(valores.get(CAMPO_END_DATE, ""))
        if not matricula and fecha is None:
            continue
        vistas[numero] = Observacion(
            log_number=numero,
            matricula=matricula,
            fecha=fecha,
            fuente="pagina en verde",
        )
    return list(vistas.values())


def bitacoras_del_libro(
    clave: str, cuantas: int = BITACORAS_POR_LIBRO
) -> List[str]:
    """Numeros de bitacora repartidos a lo largo de ese libro.

    Repartidos y no los primeros: de un libro que se publico a medias, las
    primeras paginas estan y las ultimas no, y mirando solo un extremo se
    saca la conclusion contraria a la del otro.
    """
    clave = str(clave or "").strip().upper()
    if len(clave) != 6 or not clave[:5].isdigit() or clave[5] not in "AB":
        return []
    inicio = 0 if clave[5] == "A" else PAGINAS_POR_LIBRO
    numeros = [
        f"{clave[:5]}{pagina:02d}"
        for pagina in range(inicio, inicio + PAGINAS_POR_LIBRO)
    ]
    return muestra_de(numeros, cuantas)


def observaciones_de_websearch(
    buscador: Buscador,
    claves: Sequence[str],
    cuantas: int = BITACORAS_POR_LIBRO,
    al_avanzar: Optional[Callable[[int, int], None]] = None,
) -> List[Observacion]:
    """Lo que Web Search publica de unas cuantas bitacoras de cada libro.

    Una bitacora que no aparece no dice nada y no se anota: puede que no
    este publicada o que la consulta no llegara. Solo se recoge lo que la
    respuesta trae con claridad.
    """
    observaciones: List[Observacion] = []
    total = len(claves)
    for hechos, clave in enumerate(claves, start=1):
        for numero in bitacoras_del_libro(clave, cuantas):
            indice = buscador.indice(numero)
            if indice is None or not indice.util:
                continue
            observaciones.append(Observacion(
                log_number=numero,
                matricula=normalizar_matricula(indice.matricula),
                fecha=_fecha(indice.fecha),
                fuente="Web Search",
            ))
        if al_avanzar is not None:
            al_avanzar(hechos, total)
    return observaciones


def _rutas(raiz: Path | str) -> Dict[str, Path]:
    """Los tres archivos de la instalacion que intervienen."""
    raiz = Path(raiz)
    return {
        "matriculas": raiz / BOOK_MATRICULAS_FILENAME,
        "fechas": raiz / BOOK_DATES_FILENAME,
        "flota": raiz / FLEET_FILENAME,
    }


def libros_de_la_memoria(raiz: Path | str) -> List[str]:
    """Las claves de libro que la memoria de esta instalacion conoce."""
    rutas = _rutas(raiz)
    return libros_guardados(rutas["matriculas"], rutas["fechas"])


def verificar_con_el_batch(
    paginas: Iterable[object], raiz: Path | str, escribir: bool = True
) -> Informe:
    """Comprueba la memoria con las paginas que el plan acaba de leer.

    Es la comprobacion que va sola: no pregunta nada, no pide red de mas y
    corre igual en el dry run que en el indexado, porque solo lee lo que ya
    estaba leido.
    """
    rutas = _rutas(raiz)
    # Aunque el batch no aporte ninguna pagina en verde se sigue: la
    # comprobacion contra la flota no necesita evidencia de nadie y es la
    # que descarta una matricula que no es de ningun avion.
    return verificar(
        observaciones_de_paginas(paginas),
        rutas["matriculas"],
        rutas["fechas"],
        flota=load_fleet(rutas["flota"]),
        escribir=escribir,
    )


def verificar_con_websearch(
    buscador: Buscador,
    raiz: Path | str,
    claves: Optional[Sequence[str]] = None,
    cuantas: int = BITACORAS_POR_LIBRO,
    escribir: bool = False,
    al_avanzar: Optional[Callable[[int, int], None]] = None,
) -> Informe:
    """Comprueba en Web Search los libros que la memoria ya conoce.

    Sin ``claves`` se revisan todos. Es la comprobacion que alcanza a los
    libros que no vienen en ningun batch de hoy, y la unica que ve las
    bitacoras que se publicaron sin este programa.
    """
    rutas = _rutas(raiz)
    if claves is None:
        claves = libros_de_la_memoria(raiz)
    if not claves:
        logger.info("Memoria de libros: no hay nada guardado que comprobar")
        return Informe()
    observaciones = observaciones_de_websearch(
        buscador, claves, cuantas, al_avanzar
    )
    return verificar(
        observaciones,
        rutas["matriculas"],
        rutas["fechas"],
        flota=load_fleet(rutas["flota"]),
        escribir=escribir,
    )

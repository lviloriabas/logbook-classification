"""Auditoría de la memoria de libros contra una fuente de autoridad.

El sistema recuerda de cada libro dos cosas entre ejecuciones: su matrícula
(``book_matriculas.json``) y los extremos de fecha confirmados
(``book_fechas.json``). Las dos se aprenden del propio OCR, y ahí está el
problema que este módulo resuelve: una entrada equivocada no se nota sola.
El corrector la aplica a las cincuenta páginas del libro y las deja en OK,
así que la ejecución siguiente encuentra la memoria de acuerdo con lo que
acaba de escribir y nadie descubre el error. El mapa de matrículas ni
siquiera se deja reemplazar: aprende una vez y esa asociación se queda.

La salida de ese círculo tiene que venir de fuera, y fuera está AirVault:
una página en verde es el índice que la empresa da por bueno, lo escribiera
este programa o lo corrigiera una persona en Web Index. Cuando la memoria y
AirVault dicen cosas distintas, la que se cambia es la memoria.

Aquí no se sabe nada de AirVault. Este módulo recibe observaciones ya
traducidas (:class:`Observacion`) y decide qué hacer con ellas; quien las
obtiene es :mod:`app.airvault.memoria`. Así la política de qué se corrige y
con cuánto respaldo vive junto a los dos correctores que la aplican, y se
puede probar sin red.

Las reglas son las mismas para las dos memorias:

* una observación basta para aprender un libro que no estaba y para
  confirmar el que ya coincide;
* hacen falta dos bitácoras distintas del mismo libro para **reemplazar**
  una entrada que las contradice, el mismo respaldo que el corrector exige
  para indexar sin que nadie mire: con una sola, el error podría estar en
  el número de bitácora de esa página y no en la memoria;
* si las observaciones se contradicen entre sí, no se toca nada. El
  desacuerdo está en AirVault y ninguna de las dos versiones es más creíble
  que la otra.

Confirmar no cuesta nada y corregir tampoco pregunta: la comprobación va
donde las páginas ya se leyeron, así que no gasta ni una petición de más.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
import re
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from loguru import logger

from app.validation.book_corrector import (
    _CANONICAL_MATRICULA_RE,
    _load_book_matriculas,
    _save_book_matriculas,
)
from app.validation.date_corrector import (
    RegistryAnchor,
    _load_book_dates,
    _merge_registry_anchors,
    _save_book_dates,
)
from app.validation.page_status import AUTO_INDEX_MIN_VOTES

# Bitácoras distintas del mismo libro que hacen falta para reemplazar una
# entrada guardada que las contradice. Es el respaldo que ya exigen el
# corrector de matrículas y el registro de fechas para dar por buena una
# lectura sin revisión, y no hay motivo para que la memoria se rehaga con
# menos de lo que se pide para indexar una página.
MIN_PARA_CORREGIR = AUTO_INDEX_MIN_VOTES

_LOG_NUMBER_RE = re.compile(r"^\d{7}$")

# Qué se hizo con una entrada. Son las palabras del informe, no un detalle
# interno: salen por el log y por la consola de la comprobación.
CONFIRMADO = "confirmado"
APRENDIDO = "aprendido"
AMPLIADO = "ampliado"
CORREGIDO = "corregido"
CONFLICTO = "conflicto"
INVALIDO = "invalido"


def clave_de_libro(log_number: str) -> str:
    """Clave de seis caracteres del libro al que pertenece esa bitácora.

    Es la misma que usan los dos archivos de memoria: los cinco dígitos de
    la serie y la mitad del rango de cincuenta páginas. Cadena vacía si el
    número no tiene los siete dígitos exactos.
    """
    texto = str(log_number or "").strip()
    if not _LOG_NUMBER_RE.fullmatch(texto):
        return ""
    return f"{texto[:5]}{'A' if int(texto[-2:]) < 50 else 'B'}"


@dataclass(frozen=True)
class Observacion:
    """Lo que una fuente de autoridad dice de una bitácora concreta.

    ``matricula`` y ``fecha`` pueden venir por separado: una página de
    AirVault puede tener el avión puesto y la fecha vacía, y esa mitad
    sirve igual.
    """

    log_number: str
    matricula: str = ""
    fecha: Optional[date] = None
    fuente: str = ""

    @property
    def clave(self) -> str:
        return clave_de_libro(self.log_number)

    @property
    def logpage(self) -> int:
        """Página dentro del libro, tal como la guarda el registro."""
        return int(self.log_number[-2:])


@dataclass(frozen=True)
class Cambio:
    """Qué le pasa a una entrada de la memoria y con qué respaldo."""

    clave: str
    accion: str
    guardado: str = ""
    observado: str = ""
    respaldo: int = 0
    detalle: str = ""

    def __str__(self) -> str:
        cabeza = f"{self.clave} {self.accion}"
        if self.guardado and self.guardado != self.observado:
            cabeza += f": {self.guardado} pasa a {self.observado or 'nada'}"
        elif self.observado:
            cabeza += f": {self.observado}"
        cola = []
        if self.respaldo:
            cola.append(f"{self.respaldo} bitácora(s)")
        if self.detalle:
            cola.append(self.detalle)
        return cabeza + (f" ({', '.join(cola)})" if cola else "")


@dataclass
class Informe:
    """Lo que la comprobación encontró, antes de escribir nada."""

    matriculas: List[Cambio] = field(default_factory=list)
    fechas: List[Cambio] = field(default_factory=list)
    # Lo que habría que escribir, ya resuelto, para que aplicar no tenga
    # que volver a decidir nada.
    matriculas_nuevas: Dict[str, str] = field(default_factory=dict)
    matriculas_borradas: List[str] = field(default_factory=list)
    fechas_nuevas: Dict[str, List[RegistryAnchor]] = field(default_factory=dict)
    libros: int = 0
    observaciones: int = 0

    @property
    def cambios(self) -> List[Cambio]:
        return self.matriculas + self.fechas

    @property
    def conflictos(self) -> List[Cambio]:
        """Las entradas que nadie puede resolver sin mirar la bitácora."""
        return [c for c in self.cambios if c.accion in (CONFLICTO, INVALIDO)]

    @property
    def hay_cambios(self) -> bool:
        return bool(
            self.matriculas_nuevas
            or self.matriculas_borradas
            or self.fechas_nuevas
        )

    def cuenta(self, accion: str) -> int:
        return sum(1 for cambio in self.cambios if cambio.accion == accion)

    def resumen(self) -> str:
        """Una línea con lo que salió, para el log y para la consola."""
        if not self.observaciones:
            return "Memoria de libros: AirVault no aportó ninguna bitácora"
        partes = [
            f"{self.observaciones} bitácora(s) de AirVault",
            f"{self.libros} libro(s)",
        ]
        for accion in (CONFIRMADO, APRENDIDO, AMPLIADO, CORREGIDO,
                       CONFLICTO, INVALIDO):
            cuantos = self.cuenta(accion)
            if cuantos:
                partes.append(f"{cuantos} {accion}(s)")
        return "Memoria de libros: " + ", ".join(partes)


def _por_numero(flota: Iterable[str]) -> Dict[str, str]:
    """Matrícula de la flota indexada por sus cuatro dígitos.

    El sufijo no se lee de la página: lo deduce el postproceso, y AirVault
    puede tener escrito otro distinto del que usa la flota local. Comparar
    por el número evita que un sufijo distinto se lea como otro avión, que
    es lo único que importa: el 1522 se publicó durante años como WWP y
    hoy es CMP en los dos lados, y ninguna de las dos formas es un avión
    diferente.

    Un número que la flota escribe de dos maneras se deja fuera: no hay una
    forma local que elegir.
    """
    por_numero: Dict[str, str] = {}
    repetidos: Set[str] = set()
    for matricula in flota:
        encontrada = _CANONICAL_MATRICULA_RE.fullmatch(
            str(matricula or "").strip().upper()
        )
        if encontrada is None:
            continue
        numero = encontrada.group(1)
        anterior = por_numero.get(numero)
        if anterior is not None and anterior != encontrada.group(0):
            repetidos.add(numero)
        por_numero[numero] = encontrada.group(0)
    for numero in repetidos:
        por_numero.pop(numero, None)
    return por_numero


def _como_la_escribe_la_flota(
    matricula: str, por_numero: Dict[str, str]
) -> str:
    """La matrícula con el sufijo que usa esta instalación.

    Lo que se guarda en la memoria acaba en el CSV y en los nombres de
    archivo, que conservan la matrícula como la normaliza el OCR. Traer el
    sufijo de AirVault la cambiaría para todo un avión.
    """
    encontrada = _CANONICAL_MATRICULA_RE.fullmatch(matricula or "")
    if encontrada is None:
        return ""
    return por_numero.get(encontrada.group(1), matricula)


def _agrupar(
    observaciones: Iterable[Observacion],
) -> Dict[str, List[Observacion]]:
    """Observaciones por libro, con una sola por número de bitácora.

    La misma bitácora puede llegar dos veces (dos batches que la contienen,
    dos consultas seguidas). Contarla dos veces le daría a una sola página
    el respaldo que este módulo exige de dos.
    """
    unicas: Dict[Tuple[str, str], Observacion] = {}
    for observacion in observaciones:
        clave = observacion.clave
        if not clave:
            continue
        unicas.setdefault((clave, observacion.log_number), observacion)
    agrupadas: Dict[str, List[Observacion]] = defaultdict(list)
    for (clave, _numero), observacion in sorted(unicas.items()):
        agrupadas[clave].append(observacion)
    return dict(agrupadas)


def _auditar_matriculas(
    por_libro: Dict[str, List[Observacion]],
    guardado: Dict[str, str],
    por_numero: Dict[str, str],
    informe: Informe,
) -> Set[str]:
    """Compara la matrícula guardada de cada libro con la observada.

    Devuelve los libros de los que la fuente dijo una matrícula y solo una:
    son los que ya no hace falta contrastar contra la lista de la flota.
    """
    respaldados: Set[str] = set()
    for clave in sorted(por_libro):
        numeros_por_matricula: Dict[str, Set[str]] = defaultdict(set)
        for observacion in por_libro[clave]:
            matricula = _como_la_escribe_la_flota(
                observacion.matricula, por_numero
            )
            if matricula:
                numeros_por_matricula[matricula].add(observacion.log_number)
        if not numeros_por_matricula:
            continue
        previo = guardado.get(clave, "")
        if len(numeros_por_matricula) > 1:
            # Un libro es un avión: si AirVault tiene dos, el error está
            # allí, y cambiar la memoria por cualquiera de los dos sería
            # elegir al azar.
            informe.matriculas.append(Cambio(
                clave, CONFLICTO, previo,
                ", ".join(sorted(numeros_por_matricula)),
                detalle="AirVault no dice lo mismo en todo el libro",
            ))
            continue
        matricula, numeros = next(iter(numeros_por_matricula.items()))
        respaldo = len(numeros)
        respaldados.add(clave)
        if not previo:
            informe.matriculas.append(
                Cambio(clave, APRENDIDO, "", matricula, respaldo)
            )
            informe.matriculas_nuevas[clave] = matricula
        elif previo == matricula:
            informe.matriculas.append(
                Cambio(clave, CONFIRMADO, previo, matricula, respaldo)
            )
        elif respaldo >= MIN_PARA_CORREGIR:
            informe.matriculas.append(
                Cambio(clave, CORREGIDO, previo, matricula, respaldo)
            )
            informe.matriculas_nuevas[clave] = matricula
        else:
            informe.matriculas.append(Cambio(
                clave, CONFLICTO, previo, matricula, respaldo,
                detalle="una sola bitácora no reemplaza lo guardado",
            ))
    return respaldados


def _revisar_flota(
    guardado: Dict[str, str],
    por_numero: Dict[str, str],
    respaldados: Set[str],
    informe: Informe,
) -> None:
    """Descarta las entradas cuyo avión no existe en la flota.

    No hace falta preguntarle a nadie: una matrícula que no es de ningún
    avión de la flota salió de un dígito mal leído, y mientras siga en el
    archivo el corrector se la pone a las cincuenta páginas del libro.
    Borrarla no inventa nada: el libro vuelve al consenso de cada
    ejecución, que es lo que había antes de aprenderla.

    Los libros que la fuente acaba de respaldar se dejan en paz aunque su
    avión no esté en la lista local: la lista se escribe a mano y envejece,
    y borrar lo que AirVault confirma solo conseguiría aprenderlo y volver
    a borrarlo en cada ejecución.
    """
    if not por_numero:
        return
    for clave in sorted(guardado):
        if clave in respaldados:
            continue
        matricula = guardado[clave]
        encontrada = _CANONICAL_MATRICULA_RE.fullmatch(matricula)
        if encontrada is None or encontrada.group(1) not in por_numero:
            informe.matriculas.append(Cambio(
                clave, INVALIDO, matricula, "",
                detalle="no es un avión de la flota",
            ))
            informe.matriculas_borradas.append(clave)


def _anclas_observadas(
    observaciones: Sequence[Observacion],
) -> Tuple[List[RegistryAnchor], str]:
    """Anclas del libro y por qué no sirven, si es que no sirven."""
    por_pagina: Dict[int, date] = {}
    for observacion in observaciones:
        if observacion.fecha is None:
            continue
        conocida = por_pagina.get(observacion.logpage)
        if conocida is not None and conocida != observacion.fecha:
            return [], (
                f"la página {observacion.logpage:02d} tiene dos fechas en "
                f"AirVault ({conocida.isoformat()} y "
                f"{observacion.fecha.isoformat()})"
            )
        por_pagina[observacion.logpage] = observacion.fecha
    anclas = sorted(por_pagina.items())
    if any(
        posterior < anterior
        for (_a, anterior), (_b, posterior) in zip(anclas, anclas[1:])
    ):
        return [], "las fechas de AirVault retroceden dentro del libro"
    return anclas, ""


def _describir(anclas: Sequence[RegistryAnchor]) -> str:
    """Anclas en una línea, como las escribe el archivo."""
    return ", ".join(
        f"{logpage:02d}={valor.isoformat()}"
        for logpage, valor in sorted(anclas)
    )


def _auditar_fechas(
    por_libro: Dict[str, List[Observacion]],
    guardado: Dict[str, List[RegistryAnchor]],
    informe: Informe,
) -> None:
    """Pone al día los extremos guardados con las fechas de AirVault."""
    acciones = {"nuevo": APRENDIDO, "ampliado": AMPLIADO,
                "corregido": CORREGIDO}
    for clave in sorted(por_libro):
        observadas, problema = _anclas_observadas(por_libro[clave])
        previas = guardado.get(clave, [])
        if problema:
            informe.fechas.append(Cambio(
                clave, CONFLICTO, _describir(previas), "", detalle=problema,
            ))
            continue
        if not observadas:
            continue
        conciliadas, accion = _merge_registry_anchors(
            clave, previas, observadas, origen="páginas de AirVault"
        )
        if conciliadas is None:
            informe.fechas.append(Cambio(
                clave, CONFLICTO, _describir(previas),
                _describir(observadas), len(observadas),
                detalle="una sola bitácora no reemplaza lo guardado",
            ))
            continue
        if not accion:
            informe.fechas.append(Cambio(
                clave, CONFIRMADO, _describir(previas),
                _describir(observadas), len(observadas),
            ))
            continue
        informe.fechas.append(Cambio(
            clave, acciones[accion], _describir(previas),
            _describir(conciliadas), len(observadas),
        ))
        informe.fechas_nuevas[clave] = conciliadas


def libros_guardados(
    matriculas_path: Path, fechas_path: Path
) -> List[str]:
    """Las claves de libro que la memoria conoce hoy, sin repetir."""
    claves = set(_load_book_matriculas(Path(matriculas_path)))
    claves.update(_load_book_dates(Path(fechas_path)))
    return sorted(claves)


def auditar(
    observaciones: Iterable[Observacion],
    matriculas_path: Path,
    fechas_path: Path,
    flota: Iterable[str] = (),
) -> Informe:
    """Compara la memoria guardada con lo que dice la fuente, sin escribir.

    Args:
        observaciones: lo que la fuente de autoridad dice de cada bitácora.
        matriculas_path: ``book_matriculas.json``.
        fechas_path: ``book_fechas.json``.
        flota: matrículas de los aviones que existen (``fleet.json``). Con
            ella se conserva el sufijo local y se descartan las entradas
            que no son de ningún avión.

    Returns:
        El informe, con lo que habría que escribir ya resuelto.
    """
    utiles = [
        observacion for observacion in observaciones
        if observacion.clave
        and (
            observacion.fecha is not None
            or _CANONICAL_MATRICULA_RE.fullmatch(observacion.matricula or "")
        )
    ]
    por_libro = _agrupar(utiles)
    por_numero = _por_numero(flota)
    informe = Informe(
        libros=len(por_libro),
        observaciones=sum(len(v) for v in por_libro.values()),
    )
    guardado = _load_book_matriculas(Path(matriculas_path))
    respaldados = _auditar_matriculas(por_libro, guardado, por_numero, informe)
    _revisar_flota(guardado, por_numero, respaldados, informe)
    _auditar_fechas(por_libro, _load_book_dates(Path(fechas_path)), informe)
    return informe


def aplicar(
    informe: Informe, matriculas_path: Path, fechas_path: Path
) -> Dict[str, int]:
    """Escribe en la memoria lo que el informe dejó resuelto.

    Los dos archivos se releen justo antes de escribirlos: entre auditar y
    aplicar puede haber pasado una ejecución que aprendiera otros libros, y
    la comprobación solo tiene autoridad sobre los libros que miró.
    """
    escrito = {"matriculas": 0, "fechas": 0}
    if informe.matriculas_nuevas or informe.matriculas_borradas:
        guardado = _load_book_matriculas(Path(matriculas_path))
        for clave in informe.matriculas_borradas:
            guardado.pop(clave, None)
        guardado.update(informe.matriculas_nuevas)
        try:
            _save_book_matriculas(Path(matriculas_path), guardado)
        except OSError as exc:
            logger.warning(f"No se pudo guardar el mapa de libros: {exc}")
        else:
            escrito["matriculas"] = (
                len(informe.matriculas_nuevas)
                + len(informe.matriculas_borradas)
            )
    if informe.fechas_nuevas:
        registro = _load_book_dates(Path(fechas_path))
        registro.update(informe.fechas_nuevas)
        try:
            _save_book_dates(Path(fechas_path), registro)
        except OSError as exc:
            logger.warning(f"No se pudo guardar el registro de fechas: {exc}")
        else:
            escrito["fechas"] = len(informe.fechas_nuevas)
    return escrito


def verificar(
    observaciones: Iterable[Observacion],
    matriculas_path: Path,
    fechas_path: Path,
    flota: Iterable[str] = (),
    escribir: bool = True,
) -> Informe:
    """Audita la memoria y, si se pide, la corrige. Nunca pregunta nada."""
    informe = auditar(observaciones, matriculas_path, fechas_path, flota)
    if not informe.observaciones and not informe.matriculas_borradas:
        return informe
    logger.info(informe.resumen())
    for cambio in informe.cambios:
        if cambio.accion != CONFIRMADO:
            logger.warning(f"Memoria de libros: {cambio}")
    if escribir and informe.hay_cambios:
        aplicar(informe, matriculas_path, fechas_path)
    return informe

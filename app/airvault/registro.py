"""Registro durable de los batches de una entrega.

El manifiesto de cada batch dice en que va ese batch. Lo que no dice
ninguno es que se hizo con la entrega **entera**: que bitacoras se llevo
cada uno, cuales llegaron a AirVault y cuales quedan por mandar. Esa
respuesta se sacaba juntando los manifiestos que hubiera en disco en ese
momento, y por eso cambiaba cuando cambiaba el reparto: al repartir de
nuevo con otro maximo de paginas, los manifiestos viejos se apartan y con
ellos se iba la unica memoria de lo que ya se habia subido.

Este registro es esa memoria, y es independiente de la configuracion. Vive
en la carpeta del trabajo, junto a los manifiestos, de modo que borrar el
registro local de la ejecucion lo borra tambien: es una sola memoria, y se
olvida entera o no se olvida. Guarda ademas unas cuantas versiones
anteriores, para poder mirar (o recuperar) un reparto que se descarto.

La identidad de una bitacora es la de siempre: el archivo del que salio y
su pagina dentro de el. Solo es unica dentro de su entrega, asi que el
registro anota tambien de que CSV viene.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Sequence, Set, Tuple

from pydantic import BaseModel, Field

REGISTRO_FILENAME = "registro-de-batches.json"

# Repartos anteriores que se conservan. Cada uno es la lista completa de
# batches tal como estaba antes de rehacerla, asi que sirve para ver que
# se descarto y para reconstruirlo a mano si hiciera falta. Sin tope, una
# ejecucion que se reparte muchas veces acabaria arrastrando un archivo
# que crece sin motivo.
MAXIMO_HISTORIAL = 10

_NOMBRE_DE_PARTE = re.compile(r"(parte|revisar)(-\d+)?$", re.IGNORECASE)


class BatchAnotado(BaseModel):
    """Lo que hay que recordar de un batch aunque cambie el reparto."""

    carpeta: str = ""
    parte: int = 1
    nombre_batch: str = ""
    batch_id: str = ""
    # Bitacoras que lleva, por archivo de origen y pagina dentro de el. Sin
    # separadores: una pagina divisoria no es un documento y no se indexa.
    paginas: List[Tuple[str, int]] = Field(default_factory=list)
    subido: bool = False
    indexado: bool = False
    completado: bool = False
    actualizado: str = ""

    def claves(self) -> Set[Tuple[str, int]]:
        return {(str(archivo).casefold(), int(pagina))
                for archivo, pagina in self.paginas}


class RepartoArchivado(BaseModel):
    """Un reparto anterior, guardado tal como estaba antes de rehacerlo."""

    guardado: str = ""
    motivo: str = ""
    batches: List[BatchAnotado] = Field(default_factory=list)


class RegistroDeEntrega(BaseModel):
    """Todos los batches de una entrega, con su historia reciente."""

    version: int = 1
    csv_origen: str = ""
    actualizado: str = ""
    batches: List[BatchAnotado] = Field(default_factory=list)
    historial: List[RepartoArchivado] = Field(default_factory=list)

    def por_carpeta(self) -> Dict[str, BatchAnotado]:
        return {batch.carpeta: batch for batch in self.batches}

    def comprometidas(self) -> Set[Tuple[str, int]]:
        """Bitacoras que ya viajaron a AirVault y no se vuelven a mandar."""
        claves: Set[Tuple[str, int]] = set()
        for batch in self.batches:
            if batch.subido or batch.batch_id:
                claves |= batch.claves()
        return claves

    def anotadas(self) -> Set[Tuple[str, int]]:
        """Todas las bitacoras que el registro conoce, se subieran o no."""
        claves: Set[Tuple[str, int]] = set()
        for batch in self.batches:
            claves |= batch.claves()
        return claves


def raiz_de_registro(carpeta: Path | str) -> Path:
    """La carpeta de la entrega a partir de la de un batch.

    Una entrega repartida deja cada parte en su subcarpeta (``parte-02``,
    ``revisar``) y sin repartir el trabajo vive directamente en la carpeta
    de la ejecucion. El registro es de la entrega, asi que siempre sube a
    esa carpeta comun.
    """
    carpeta = Path(carpeta)
    if _NOMBRE_DE_PARTE.fullmatch(carpeta.name):
        return carpeta.parent
    return carpeta


def ruta_registro(carpeta: Path | str) -> Path:
    return raiz_de_registro(carpeta) / REGISTRO_FILENAME


def leer(carpeta: Path | str) -> RegistroDeEntrega:
    """El registro de la entrega, o uno vacio si todavia no hay ninguno.

    Un archivo ilegible se trata como ausente en vez de cortar el trabajo:
    el registro acelera y protege, pero los manifiestos siguen estando y
    con ellos se puede reconstruir.
    """
    ruta = ruta_registro(carpeta)
    if not ruta.is_file():
        return RegistroDeEntrega()
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
        return RegistroDeEntrega.model_validate(datos)
    except (OSError, ValueError):
        return RegistroDeEntrega()


def guardar(registro: RegistroDeEntrega, carpeta: Path | str) -> Path:
    """Escribe el registro de forma atomica, como el manifiesto."""
    destino = ruta_registro(carpeta)
    destino.parent.mkdir(parents=True, exist_ok=True)
    registro.actualizado = datetime.now().isoformat(timespec="seconds")
    contenido = registro.model_dump_json(indent=2)
    tmp_fd, tmp_name = tempfile.mkstemp(
        dir=str(destino.parent), prefix=".registro-", suffix=".tmp"
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as handle:
            handle.write(contenido)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, destino)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return destino


def _anotacion_de(trabajo) -> BatchAnotado:
    """Lo que el registro guarda de un trabajo, leido de su manifiesto."""
    manifiesto = trabajo.manifiesto
    paginas = [
        (registro.archivo_origen, int(registro.pagina_origen))
        for registro in manifiesto.registros
        if not registro.es_separador and registro.archivo_origen
    ]
    return BatchAnotado(
        carpeta=str(trabajo.carpeta),
        parte=int(manifiesto.parte or 1),
        nombre_batch=manifiesto.nombre_batch,
        batch_id=str(manifiesto.batch_id or ""),
        paginas=paginas,
        subido=manifiesto.etapa_hecha("subir"),
        indexado=manifiesto.etapa_hecha("verificar"),
        completado=manifiesto.etapa_hecha("completar"),
        actualizado=datetime.now().isoformat(timespec="seconds"),
    )


def anotar(
    carpeta: Path | str,
    trabajos: Sequence,
    csv_origen: str = "",
) -> RegistroDeEntrega:
    """Deja constancia de en que van estos batches.

    Actualiza los que se le pasan y **conserva** los demas. Es la parte que
    importa: un batch cuyo manifiesto se aparto al rehacer el reparto sigue
    anotado con las bitacoras que se llevo, asi que no se vuelven a subir
    aunque su manifiesto ya no este en disco.
    """
    registro = leer(carpeta)
    existentes = registro.por_carpeta()
    for trabajo in trabajos:
        anotacion = _anotacion_de(trabajo)
        anterior = existentes.get(anotacion.carpeta)
        if anterior is not None:
            # Lo que ya llego a AirVault no se desanota porque un manifiesto
            # nuevo todavía no lo diga: se suman, nunca se restan.
            anotacion.subido = anotacion.subido or anterior.subido
            anotacion.indexado = anotacion.indexado or anterior.indexado
            anotacion.completado = anotacion.completado or anterior.completado
            anotacion.batch_id = anotacion.batch_id or anterior.batch_id
        existentes[anotacion.carpeta] = anotacion
    if csv_origen:
        registro.csv_origen = str(csv_origen)
    elif not registro.csv_origen and trabajos:
        registro.csv_origen = str(trabajos[0].manifiesto.csv_origen or "")
    registro.batches = sorted(
        existentes.values(), key=lambda batch: (batch.parte, batch.carpeta)
    )
    guardar(registro, carpeta)
    return registro


def archivar(carpeta: Path | str, motivo: str = "") -> RegistroDeEntrega:
    """Guarda el reparto vigente en el historial antes de rehacerlo."""
    registro = leer(carpeta)
    if not registro.batches:
        return registro
    registro.historial.insert(
        0,
        RepartoArchivado(
            guardado=datetime.now().isoformat(timespec="seconds"),
            motivo=motivo,
            batches=list(registro.batches),
        ),
    )
    del registro.historial[MAXIMO_HISTORIAL:]
    guardar(registro, carpeta)
    return registro


def comprometidas(carpeta: Path | str) -> Set[Tuple[str, int]]:
    """Bitacoras que el registro da por enviadas a AirVault."""
    return leer(carpeta).comprometidas()


def rutas_del_registro(carpeta: Path | str) -> List[Path]:
    """Archivos de memoria local que se van con el registro de la entrega.

    Son el registro y los manifiestos apartados al rehacer un reparto. Los
    manifiestos vivos los enumera quien los borra; estos no aparecen en esa
    busqueda porque ya no se llaman ``manifiesto.json``, y quedarse serian
    los unicos restos de una ejecucion que se pidio olvidar.
    """
    raiz = raiz_de_registro(carpeta)
    if not raiz.is_dir():
        return []
    rutas = [ruta for ruta in [ruta_registro(raiz)] if ruta.is_file()]
    rutas.extend(
        sorted(
            ruta for ruta in raiz.rglob("manifiesto-reemplazado-*.json")
            if ruta.is_file()
        )
    )
    return rutas

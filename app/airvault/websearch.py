"""Consulta a Web Search para saber si una bitacora ya esta publicada.

Todas las defensas contra duplicados que existian antes miran la **cola**
de Web Index: los batches que estan esperando a que alguien los indexe. El
problema es que un batch completado sale de esa cola y se va a Web Search,
asi que a partir de ese momento el programa deja de verlo. Si la memoria
local se pierde o se borra a proposito, nada impide volver a subir lo mismo:
la cola esta limpia y el manifiesto ya no existe. Ese es justo el hueco por
el que se cuelan los duplicados.

Web Search si tiene esas bitacoras, y las tiene por su numero de bitacora,
que es la identidad de verdad: no depende de como se llamo el batch, ni de
en que carpeta se preparo, ni de si el reparto se rehizo.

**El endpoint no esta documentado.** AirVault no publica su API, asi que la
ruta de busqueda se descubre en ejecucion: se lee la portada de ``/zfp/``,
se sacan las rutas que aparecen en ella y en sus scripts, se prueban las que
parecen de busqueda y se conserva la que funciona en el JSON portable, para
no repetir el recorrido en cada consulta.

Un endpoint que no se ha probado no sirve para decir «esta bitacora no
esta»: una ruta equivocada tambien contesta que no hay nada. Por eso una
ruta solo se da por buena cuando encuentra un **control positivo**, un
numero de bitacora que el propio programa completo antes y que por lo tanto
tiene que estar publicado. Sin ese control, la respuesta se devuelve como
indeterminada y quien pregunta la trata como un «no se pudo comprobar», que
nunca autoriza nada.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Sequence

from loguru import logger

from app.airvault.config import (
    CAMPO_LOG_NUMBER,
    AirVaultConfig,
    guardar_ruta_websearch,
)

# Portada del modulo. De aqui salen la ruta base y los scripts que se leen.
PORTADA = "/zfp/"

# Cuantas bitacoras del batch se consultan para decidir si ya esta subido.
# No hacen falta todas: si tres numeros distintos del mismo batch ya estan
# publicados, el batch esta publicado. Y si el batch se subio a medias, con
# tres repartidas por el se nota igual.
MUESTRA_POR_BATCH = 3

# Rutas que se prueban al final, cuando la portada no delata ninguna. Son
# las del patron de controlador que AirVault usa en el resto de sus modulos
# (``/index/Batch/GetBatches``, ``/quickuploadex/Home/GetRepositories``), y
# probarlas cuesta una peticion que se descarta sola si no contesta JSON.
RUTAS_CONOCIDAS = (
    "/zfp/Search/GetSearchResults",
    "/zfp/Search/Search",
    "/zfp/Search/GetResults",
    "/zfp/Home/Search",
    "/zfp/Home/GetSearchResults",
    "/zfp/Document/Search",
)

# Que en una ruta la delata como la busqueda y no como otra cosa del modulo.
_VERBOS = ("search", "query", "find", "result", "lookup")
# Lo que descarta una ruta aunque lleve uno de esos verbos: guardar una
# busqueda, borrarla o exportarla no es consultarla, y probarlas escribiria
# en la cuenta de la persona.
_PROHIBIDOS = (
    "save", "delete", "remove", "update", "insert", "create", "export",
    "download", "print", "email", "share", "upload",
)

_RUTA_EN_TEXTO = re.compile(r"[\"'](/?zfp/[A-Za-z0-9_/.\-]+)[\"']")
_SCRIPT_EN_HTML = re.compile(
    r"<script[^>]+src\s*=\s*[\"']([^\"']+)[\"']", re.IGNORECASE
)


def _b64(texto: str) -> str:
    return base64.b64encode(str(texto).encode("utf-8")).decode("ascii")


@dataclass(frozen=True)
class Plantilla:
    """Una forma de preguntar: como se llaman los parametros de la ruta.

    AirVault no usa los mismos nombres en todos sus modulos, asi que en vez
    de adivinar uno se prueban los que ya se le conocen. La que conteste se
    guarda junto con la ruta y no se vuelve a probar.
    """

    nombre: str
    # ``valor`` es el numero de bitacora que se busca; ``config`` da repoId.
    construir: Callable[[str, AirVaultConfig], Dict[str, object]]


def _plantillas() -> List[Plantilla]:
    """Las formas conocidas de mandar una busqueda por Log Page Number."""
    campo = CAMPO_LOG_NUMBER
    return [
        Plantilla(
            "encodedValues",
            lambda valor, config: {
                "repoId": config.repo_id,
                "encodedValues": _b64(f"{campo}={valor}"),
                "rows": 50, "page": 1,
            },
        ),
        Plantilla(
            "encodedKeywordFilter",
            lambda valor, config: {
                "repoId": config.repo_id,
                "encodedKeywordFilter": _b64(str(valor)),
                "encodedFilter": "",
                "rows": 50, "page": 1,
            },
        ),
        Plantilla(
            "fieldId",
            lambda valor, config: {
                "repoId": config.repo_id,
                "fieldId": campo,
                "value": valor,
                "rows": 50, "page": 1,
            },
        ),
        Plantilla(
            "searchText",
            lambda valor, config: {
                "repoId": config.repo_id,
                "searchText": valor,
                "rows": 50, "page": 1,
            },
        ),
    ]


PLANTILLAS = {plantilla.nombre: plantilla for plantilla in _plantillas()}


def _ruta_absoluta(ruta: str) -> str:
    ruta = str(ruta or "").strip()
    if not ruta:
        return ""
    if ruta.startswith("//") or "://" in ruta:
        return ""
    return ruta if ruta.startswith("/") else f"/{ruta}"


def _parece_busqueda(ruta: str) -> bool:
    bajo = ruta.lower()
    if any(malo in bajo for malo in _PROHIBIDOS):
        return False
    if bajo.endswith((".js", ".css", ".png", ".gif", ".jpg", ".svg", ".map")):
        return False
    return any(verbo in bajo for verbo in _VERBOS)


def _rutas_del_texto(texto: str) -> List[str]:
    """Rutas de ``/zfp/`` que aparecen escritas en una pagina o un script."""
    vistas: List[str] = []
    for hallada in _RUTA_EN_TEXTO.findall(texto or ""):
        ruta = _ruta_absoluta(hallada)
        if ruta and _parece_busqueda(ruta) and ruta not in vistas:
            vistas.append(ruta)
    return vistas


def candidatas(sesion, limite_scripts: int = 12) -> List[str]:
    """Rutas de busqueda que la portada de Web Search deja ver.

    Primero las que estan escritas en la propia pagina, despues las de sus
    scripts, y al final las que ya se le conocen al patron de AirVault. El
    orden importa: lo que el modulo declara de si mismo vale mas que una
    lista escrita aqui, que envejece.
    """
    encontradas: List[str] = []

    def sumar(rutas: Iterable[str]) -> None:
        for ruta in rutas:
            if ruta not in encontradas:
                encontradas.append(ruta)

    try:
        portada = sesion.get(PORTADA, json_esperado=False)
    except Exception as exc:  # noqa: BLE001 - sin portada quedan las conocidas
        logger.info("No se pudo leer la portada de Web Search: {}", exc)
        portada = ""
    if not isinstance(portada, str):
        portada = ""
    sumar(_rutas_del_texto(portada))

    for src in _SCRIPT_EN_HTML.findall(portada)[:limite_scripts]:
        ruta = _ruta_absoluta(src.split("?", 1)[0])
        if not ruta or not ruta.lower().endswith(".js"):
            continue
        try:
            guion = sesion.get(ruta, json_esperado=False)
        except Exception as exc:  # noqa: BLE001 - un script menos, no un fallo
            logger.debug("No se pudo leer {}: {}", ruta, exc)
            continue
        sumar(_rutas_del_texto(guion if isinstance(guion, str) else ""))

    sumar(RUTAS_CONOCIDAS)
    return encontradas


def _textos(datos: object, profundidad: int = 0) -> Iterable[str]:
    """Todo lo que en una respuesta puede ser un valor de campo."""
    if profundidad > 6:
        return
    if isinstance(datos, str):
        yield datos
    elif isinstance(datos, bool):
        return
    elif isinstance(datos, (int, float)):
        yield str(datos)
    elif isinstance(datos, Mapping):
        for valor in datos.values():
            yield from _textos(valor, profundidad + 1)
    elif isinstance(datos, (list, tuple)):
        for valor in datos:
            yield from _textos(valor, profundidad + 1)


def _aparece(numero: str, datos: object) -> bool:
    """Si la respuesta trae ese numero de bitacora como un valor entero.

    Se compara contra los valores y no contra el JSON en crudo: un numero
    de siete digitos aparece por casualidad dentro de un identificador
    largo, y eso daria por publicada una bitacora que no lo esta.
    """
    buscado = str(numero).strip()
    if not buscado:
        return False
    for texto in _textos(datos):
        if buscado == str(texto).strip():
            return True
    return False


@dataclass
class Consulta:
    """Como quedo una pregunta a Web Search."""

    # ``True`` publicada, ``False`` no publicada, ``None`` no se pudo saber.
    publicada: Optional[bool]
    motivo: str = ""


@dataclass
class Buscador:
    """La conexion con Web Search, con su ruta ya probada o por probar.

    Se construye una vez por ejecucion y se reutiliza: descubrir la ruta
    cuesta varias peticiones y no cambia entre batches.
    """

    sesion: object
    config: AirVaultConfig
    ruta_config: Optional[Path] = None
    # Numeros de bitacora que se sabe publicados, para probar la ruta. Sin
    # al menos uno, ninguna respuesta negativa se da por buena.
    controles: Sequence[str] = ()
    _ruta: str = ""
    _plantilla: str = ""
    _probado: bool = False
    _motivo: str = ""
    _memoria: Dict[str, bool] = field(default_factory=dict)

    # ── ruta ───────────────────────────────────────────────────────

    def _guardada(self) -> tuple[str, str]:
        ruta = str(self.config.ruta_websearch or "").strip()
        plantilla = str(self.config.parametros_websearch or "").strip()
        if ruta and plantilla in PLANTILLAS:
            return ruta, plantilla
        return "", ""

    def _pedir(self, ruta: str, plantilla: str, valor: str):
        forma = PLANTILLAS[plantilla]
        return self.sesion.get(ruta, forma.construir(valor, self.config))

    def _probar(self, ruta: str, plantilla: str) -> bool:
        """Si esa ruta encuentra un control positivo.

        Encontrarlo es la unica prueba que sirve. Que conteste JSON solo
        dice que la ruta existe, no que este mirando donde hay que mirar.
        """
        for control in self.controles:
            try:
                datos = self._pedir(ruta, plantilla, control)
            except Exception as exc:  # noqa: BLE001 - se prueba la siguiente
                logger.debug("Web Search {} ({}): {}", ruta, plantilla, exc)
                return False
            if _aparece(control, datos):
                return True
        return False

    def preparar(self) -> bool:
        """Deja lista la ruta de busqueda; dice si se puede consultar."""
        if self._probado:
            return bool(self._ruta)
        self._probado = True
        if not self.controles:
            self._motivo = (
                "todavia no hay ninguna bitacora completada con la que "
                "comprobar que la busqueda responde"
            )
            return False
        ruta, plantilla = self._guardada()
        if ruta and self._probar(ruta, plantilla):
            self._ruta, self._plantilla = ruta, plantilla
            return True
        for candidata in candidatas(self.sesion):
            for nombre in PLANTILLAS:
                if self._probar(candidata, nombre):
                    self._ruta, self._plantilla = candidata, nombre
                    logger.info(
                        "Web Search responde en {} con {}", candidata, nombre
                    )
                    if self.ruta_config is not None:
                        guardar_ruta_websearch(
                            self.ruta_config, candidata, nombre
                        )
                    return True
        self._motivo = (
            "no se encontro en Web Search ninguna consulta que devuelva las "
            "bitacoras ya publicadas"
        )
        logger.info("Web Search: {}", self._motivo)
        return False

    @property
    def ruta(self) -> str:
        return self._ruta

    @property
    def motivo(self) -> str:
        return self._motivo

    # ── consulta ───────────────────────────────────────────────────

    def publicada(self, numero: str) -> Consulta:
        """Si esa bitacora ya esta en Web Search."""
        numero = str(numero or "").strip()
        if not numero:
            return Consulta(None, "la bitacora no trae numero")
        if numero in self._memoria:
            return Consulta(self._memoria[numero])
        if not self.preparar():
            return Consulta(None, self._motivo)
        try:
            datos = self._pedir(self._ruta, self._plantilla, numero)
        except Exception as exc:  # noqa: BLE001 - una consulta que no sale
            motivo = f"Web Search no contesto: {exc}"
            logger.info(motivo)
            return Consulta(None, motivo)
        resultado = _aparece(numero, datos)
        self._memoria[numero] = resultado
        return Consulta(resultado)


def muestra_de(
    numeros: Sequence[str], cuantas: int = MUESTRA_POR_BATCH
) -> List[str]:
    """Bitacoras repartidas a lo largo del batch, no las primeras.

    Un batch que se subio a medias tiene publicadas las de arriba y no las
    de abajo. Mirando solo el principio se daria por publicado entero; y
    mirando solo el final, por no publicado. Repartidas se nota cual de las
    dos cosas paso.
    """
    unicos: List[str] = []
    for numero in numeros:
        limpio = str(numero or "").strip()
        if limpio and limpio not in unicos:
            unicos.append(limpio)
    if cuantas <= 0:
        return []
    if len(unicos) <= cuantas:
        return unicos
    if cuantas == 1:
        return [unicos[0]]
    paso = (len(unicos) - 1) / (cuantas - 1)
    return [unicos[round(indice * paso)] for indice in range(cuantas)]


@dataclass
class Veredicto:
    """Que dice Web Search de las bitacoras de un batch."""

    # Numeros consultados que ya estaban publicados.
    publicadas: List[str] = field(default_factory=list)
    # Numeros consultados que no aparecieron.
    ausentes: List[str] = field(default_factory=list)
    # Por que no se pudo consultar, si es que no se pudo.
    motivo: str = ""

    @property
    def concluyente(self) -> bool:
        """Si la consulta llego a responder algo de lo que fiarse."""
        return bool(self.publicadas or self.ausentes)

    @property
    def ya_publicado(self) -> bool:
        """Si hay que dar el batch por subido y no volver a mandarlo."""
        return bool(self.publicadas)

    def resumen(self, cuantas: int = 3) -> str:
        if self.publicadas:
            lista = ", ".join(self.publicadas[:cuantas])
            resto = len(self.publicadas) - cuantas
            cola = f" y {resto} mas" if resto > 0 else ""
            return f"Web Search ya tiene publicadas las bitacoras {lista}{cola}"
        if self.ausentes:
            return "Web Search no tiene ninguna de las bitacoras consultadas"
        return self.motivo or "no se pudo consultar Web Search"


def revisar_batch(
    buscador: Buscador,
    numeros: Sequence[str],
    cuantas: int = MUESTRA_POR_BATCH,
) -> Veredicto:
    """Consulta unas cuantas bitacoras del batch y resume lo que salio."""
    muestra = muestra_de(numeros, cuantas)
    if not muestra:
        return Veredicto(
            motivo="el batch no trae numeros de bitacora que buscar"
        )
    veredicto = Veredicto()
    for numero in muestra:
        consulta = buscador.publicada(numero)
        if consulta.publicada is True:
            veredicto.publicadas.append(numero)
        elif consulta.publicada is False:
            veredicto.ausentes.append(numero)
        elif not veredicto.motivo:
            veredicto.motivo = consulta.motivo
    return veredicto


def numeros_de(trabajo) -> List[str]:
    """Los numeros de bitacora que lleva un batch, en su orden."""
    return [
        str(registro.log_number).strip()
        for registro in trabajo.manifiesto.registros
        if not registro.es_separador and str(registro.log_number or "").strip()
    ]

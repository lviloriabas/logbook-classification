"""Recorrido completo de un trabajo de indexado, sin interfaz.

La linea de comandos y la ventana principal hacen lo mismo con distinta
cara, asi que el orden de las etapas y las condiciones para pasar de una a
la siguiente viven aqui una sola vez. Lo que decide si una pagina se toca o
no sigue estando en :mod:`app.airvault.guards` y :mod:`app.airvault.indexer`,
que son los que se prueban a fondo.

Ninguna funcion de este modulo abre sesion ni construye clientes: los
recibe. Asi el recorrido entero se puede ejercer con el cliente falso de
los tests, que es como se comprueba que un ensayo no escribe nada.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Mapping, Optional, Sequence, Tuple

from loguru import logger

from app.airvault import manifest as manifiestos
from app.airvault.config import AirVaultConfig
from app.airvault.client import ResumenLote
from app.airvault.discovery import (
    LoteAmbiguo,
    LoteNoEncontrado,
    buscar_por_id,
    normalizar_nombre,
    recien_llegados,
)
from app.airvault.discovery import esperar as esperar_lote
from app.airvault.discovery import buscar as buscar_lote
from app.airvault.discovery import buscar_nuevo as buscar_lote_nuevo
from app.airvault.indexer import Indexador, Plan, Resultado, verificar_lote
from app.airvault.mapping import (
    ResolutorFlota,
    leer_csv_corrida,
    leer_indice_paginas,
    registros_desde_csv,
    registros_desde_entrega,
    valores_de_indice,
)
from app.airvault.model import EstadoEtapa, EstadoRegistro, Etapa, Manifiesto
from app.airvault.naming import (
    PREFIJO_POR_DEFECTO,
    nombre_de_parte,
    nombre_de_revisar,
    nombre_desde_corrida,
)

CARPETA_TRABAJOS = Path("output") / "airvault"

# AirVault/Quick Upload trabaja de forma mas estable con lotes acotados.
# La ventana deja cambiarlo antes de preparar la carga.
PAGINAS_POR_BATCH_POR_DEFECTO = 300

# Avisos de avance: reciben un texto y, cuando se sabe, cuanto se lleva de
# cuanto. Es lo que la interfaz convierte en barra de progreso.
Aviso = Callable[[str, int, int], None]


class ErrorDeCorrida(RuntimeError):
    """La corrida no trae lo que hace falta para indexarla."""


def paginas_de_lote(info) -> int:
    """Cuantas paginas dice AirVault que tiene el lote recien abierto.

    Devuelve 0 cuando la respuesta no lo trae, que es una situacion real
    —lote a medio procesar, lote borrado— y la guarda de cantidad explica
    mejor que un error de atributo a mitad de camino.
    """
    if not isinstance(info, Mapping):
        return 0
    try:
        return int(info.get("pageCount", 0) or 0)
    except (TypeError, ValueError):
        return 0


def carpeta_de_trabajo(job: str, raiz: Optional[Path] = None) -> Path:
    """Carpeta donde vive el manifiesto y los reportes de un trabajo."""
    base = Path(raiz) if raiz is not None else CARPETA_TRABAJOS
    return base / str(job).strip()


def carpeta_de_corrida(csv: Path | str) -> Path:
    """Carpeta de la corrida a partir de su CSV (``<corrida>/datos/x.CSV``)."""
    ruta = Path(csv).resolve()
    return ruta.parent.parent if ruta.parent.name == "datos" else ruta.parent


def ruta_indice_paginas(csv: Path | str) -> Path:
    """Indice de paginas que la corrida deja junto al CSV."""
    from app.reports.organize import NOMBRE_INDICE_PAGINAS

    ruta = Path(csv)
    return ruta.with_name(f"{ruta.stem}{NOMBRE_INDICE_PAGINAS}")


def pdfs_de_corrida(csv: Path | str) -> List[Path]:
    """PDFs de entrega que dejo la corrida, en orden de nombre."""
    carpeta = carpeta_de_corrida(csv)
    if not carpeta.is_dir():
        return []
    return sorted(p for p in carpeta.glob("*.pdf") if p.is_file())


@dataclass(frozen=True)
class ParteDeEntrega:
    """Un archivo de la entrega, que sera un lote propio en AirVault."""

    indice: int
    total: int
    pdf: Path
    paginas: List[dict]
    # El archivo con las bitacoras sin avion confirmado. Se sube igual que
    # los demas, pero no se indexa.
    revisar: bool = False

    def nombre_lote(self, base: str) -> str:
        if self.revisar:
            return nombre_de_parte(
                nombre_de_revisar(base), self.indice, self.total
            )
        return nombre_de_parte(base, self.indice, self.total)


def partes_de_corrida(csv: Path | str) -> List[ParteDeEntrega]:
    """Archivos de entrega de la corrida, con lo que lleva cada uno.

    Sale del indice que escribe la exportacion, no de listar la carpeta: el
    indice dice ademas en que orden van las paginas dentro de cada archivo,
    que es lo unico que permite emparejarlas con el lote sin adivinar.
    """
    indice = leer_indice_paginas(ruta_indice_paginas(csv))
    if not indice:
        return []
    carpeta = carpeta_de_corrida(csv)
    # Revisar lleva su cuenta aparte: nunca corre la numeracion de los
    # automaticos, aunque el limite de Quick Upload obligue a trocearlo.
    numerables = sum(1 for p in indice if not p.get("revisar"))
    revisables = sum(1 for p in indice if p.get("revisar"))
    partes: List[ParteDeEntrega] = []
    numero = 0
    numero_revisar = 0
    for parte in indice:
        nombre = str(parte.get("pdf", "")).strip()
        es_revisar = bool(parte.get("revisar"))
        if es_revisar:
            numero_revisar += 1
            indice_parte, total_partes = numero_revisar, revisables
        else:
            numero += 1
            indice_parte, total_partes = numero, numerables
        partes.append(ParteDeEntrega(
            indice=indice_parte, total=total_partes,
            pdf=carpeta / nombre if nombre else carpeta,
            paginas=list(parte.get("paginas") or []),
            revisar=es_revisar,
        ))
    return partes


def comprobar_entrega(csv: Path | str) -> List[ParteDeEntrega]:
    """Partes de la corrida, o un error que explica que le falta."""
    partes = partes_de_corrida(csv)
    if not partes:
        if pdfs_de_corrida(csv):
            raise ErrorDeCorrida(
                "La corrida se exporto antes de que existiera el indice de "
                "paginas. Hay que volver a exportarla para poder indexarla."
            )
        raise ErrorDeCorrida(
            "La corrida no tiene ningun PDF de entrega. Hay que exportarla "
            "antes de subirla a AirVault."
        )
    faltan = [p.pdf.name for p in partes if not p.pdf.is_file()]
    if faltan:
        raise ErrorDeCorrida(
            f"El indice nombra archivos que no estan en la carpeta de la "
            f"corrida: {', '.join(faltan[:4])}. Volver a exportarla."
        )
    return partes


def _paginas_del_pdf(ruta: Path) -> int:
    """Cuenta paginas sin dejar el documento abierto."""
    import pymupdf as fitz

    documento = fitz.open(str(ruta))
    try:
        return int(documento.page_count)
    finally:
        documento.close()


def _pdf_de_carga(
    origen: ParteDeEntrega, numero_origen: int, inicio: int, fin: int,
    carpeta: Path,
) -> Path:
    """Copia un tramo contiguo a un PDF interno y estable de Quick Upload."""
    from app.vision.pdf_loader import copy_pdf_pages

    huella = hashlib.sha1(
        str(origen.pdf.resolve()).casefold().encode("utf-8")
    ).hexdigest()[:10]
    clase = "revisar" if origen.revisar else "automatico"
    destino = (
        carpeta / "cargas" /
        f"{clase}-{numero_origen:02d}-{huella}-"
        f"p{inicio + 1:05d}-{fin:05d}.pdf"
    )
    esperadas = fin - inicio
    if destino.is_file():
        try:
            if _paginas_del_pdf(destino) == esperadas:
                return destino
        except Exception:  # noqa: BLE001 - se vuelve a generar abajo
            pass

    destino.parent.mkdir(parents=True, exist_ok=True)
    descriptor, nombre_temporal = tempfile.mkstemp(
        dir=str(destino.parent), prefix=".carga-", suffix=".pdf"
    )
    os.close(descriptor)
    temporal = Path(nombre_temporal)
    temporal.unlink(missing_ok=True)
    try:
        copy_pdf_pages(
            ((origen.pdf, pagina) for pagina in range(inicio + 1, fin + 1)),
            temporal,
        )
        os.replace(temporal, destino)
    finally:
        temporal.unlink(missing_ok=True)
    return destino


def partes_para_airvault(
    partes: Sequence[ParteDeEntrega], carpeta: Path | str,
    paginas_por_batch: int = PAGINAS_POR_BATCH_POR_DEFECTO,
    avisar: Optional[Aviso] = None,
) -> List[ParteDeEntrega]:
    """Acota los PDF que recibira Quick Upload sin tocar la entrega original.

    Cada tramo conserva el mismo orden y los mismos diccionarios del indice
    de paginas. Solo los archivos que exceden el limite se copian a la
    carpeta interna del trabajo; los que ya caben se usan directamente.
    Automaticos y REVISAR se numeran por separado.
    """
    try:
        limite = int(paginas_por_batch)
    except (TypeError, ValueError) as exc:
        raise ErrorDeCorrida(
            "El limite de paginas por batch no es valido"
        ) from exc
    if limite < 0:
        raise ErrorDeCorrida(
            "El limite de paginas por batch no puede ser negativo"
        )

    crudas: List[tuple[Path, List[dict], bool]] = []
    total_paginas = sum(len(parte.paginas) for parte in partes)
    preparadas = 0
    for numero_origen, parte in enumerate(partes, start=1):
        cantidad = len(parte.paginas)
        if limite <= 0 or cantidad <= limite:
            crudas.append((parte.pdf, list(parte.paginas), parte.revisar))
            preparadas += cantidad
            continue

        paginas_pdf = _paginas_del_pdf(parte.pdf)
        if paginas_pdf != cantidad:
            raise ErrorDeCorrida(
                f"El PDF {parte.pdf.name} tiene {paginas_pdf} paginas y su "
                f"indice declara {cantidad}; no se puede repartir sin "
                "desalinear las bitacoras. Vuelva a exportar la ejecucion."
            )
        for inicio in range(0, cantidad, limite):
            fin = min(inicio + limite, cantidad)
            if avisar is not None:
                avisar(
                    f"Preparando batches de hasta {limite} páginas",
                    preparadas, total_paginas,
                )
            pdf = _pdf_de_carga(
                parte, numero_origen, inicio, fin, Path(carpeta)
            )
            crudas.append((pdf, list(parte.paginas[inicio:fin]), parte.revisar))
            preparadas += fin - inicio

    resultado: List[ParteDeEntrega] = []
    for revisar in (False, True):
        propias = [p for p in crudas if p[2] is revisar]
        for indice, (pdf, paginas, _revisar) in enumerate(propias, start=1):
            resultado.append(ParteDeEntrega(
                indice=indice, total=len(propias), pdf=pdf,
                paginas=paginas, revisar=revisar,
            ))
    return resultado


@dataclass(frozen=True)
class ResultadoCompletar:
    """Como quedo el intento de dar un lote por terminado."""

    completado: bool
    # Paginas que impiden cerrarlo, por numero de pagina del lote.
    bloqueadas: List[int]
    paginas: int
    detalle: str = ""
    # Separadores del PDF que se quitaron del lote para poder cerrarlo.
    quitadas: List[int] = field(default_factory=list)


def _enumerar(numeros: Sequence[int], cuantos: int = 8) -> str:
    """Lista corta de paginas para un mensaje, sin volcarlas todas."""
    cabeza = ", ".join(str(n) for n in list(numeros)[:cuantos])
    resto = len(numeros) - cuantos
    return f"{cabeza} y {resto} mas" if resto > 0 else cabeza


# ── en que va cada parte en AirVault ───────────────────────────────
#
# Un lote recien subido no esta listo al instante: AirVault lo procesa en
# su cola y puede tardar minutos u horas. Estos son los estados por los
# que pasa, y son lo que se consulta cada tanto en vez de dejar el
# programa esperando delante.

SIN_SUBIR = "sin_subir"
BUSCANDO = "buscando"
PROCESANDO = "procesando"
LISTO = "listo"
TOMADO = "tomado"
SOLO_REVISAR = "solo_revisar"
INDEXADO = "indexado"
COMPLETADO = "completado"

NOMBRE_ESTADO_PARTE = {
    SIN_SUBIR: "Sin subir",
    BUSCANDO: "Subido; esperando a AirVault",
    PROCESANDO: "Procesandose en AirVault",
    LISTO: "Listo para indexar",
    TOMADO: "Abierto por otra persona",
    SOLO_REVISAR: "Para revisar a mano",
    INDEXADO: "Indexado",
    COMPLETADO: "Terminado",
}


@dataclass(frozen=True)
class EstadoParte:
    """En que va una parte de la entrega, ahora mismo, en AirVault."""

    trabajo: "Trabajo"
    estado: str
    detalle: str = ""
    lote: Optional[ResumenLote] = None

    @property
    def nombre(self) -> str:
        return self.trabajo.manifiesto.nombre_batch

    @property
    def batch_id(self) -> str:
        return self.trabajo.manifiesto.batch_id or ""

    @property
    def paginas(self) -> int:
        return self.lote.paginas if self.lote else 0

    @property
    def se_puede_indexar(self) -> bool:
        return self.estado == LISTO

    @property
    def se_acabo(self) -> bool:
        """Ya no hay nada que esperar de esta parte."""
        return self.estado in (SOLO_REVISAR, INDEXADO, COMPLETADO)

    def __str__(self) -> str:
        titulo = NOMBRE_ESTADO_PARTE.get(self.estado, self.estado)
        return f"{titulo}: {self.detalle}" if self.detalle else titulo


class Trabajo:
    """Un trabajo de indexado: su manifiesto y las etapas que lo mueven."""

    def __init__(self, config: AirVaultConfig, carpeta: Path,
                 manifiesto: Manifiesto):
        self.config = config
        self.carpeta = Path(carpeta)
        self.manifiesto = manifiesto
        # Si el lote esta tomado ahora mismo por este trabajo. Soltarlo dos
        # veces es un error del servidor, no una limpieza de mas.
        self._tomado = False

    # ── ciclo de vida ──────────────────────────────────────────────

    @classmethod
    def preparar(
        cls, config: AirVaultConfig, carpeta: Path | str, csv: Path | str,
        nombre_lote: str = "", prefijo: str = PREFIJO_POR_DEFECTO,
        resolutor: Optional[ResolutorFlota] = None,
        parte: Optional[ParteDeEntrega] = None,
        paginas_por_batch: int = PAGINAS_POR_BATCH_POR_DEFECTO,
    ) -> "Trabajo":
        """Arma el manifiesto de un archivo de entrega.

        El orden manda el PDF, no el CSV: el archivo que se sube lleva
        separadores entre las secciones y el lote de AirVault tendra una
        pagina por cada uno. Si se contaran solo las bitacoras, todo lo que
        va detras del primer separador se escribiria una pagina corrida.

        Sin ``parte`` se toma la de la corrida, y se exige que sea una sola:
        con varias hay varios lotes y el reparto lo hace
        :func:`preparar_partes`.
        """
        resolutor = resolutor or ResolutorFlota()
        filas = leer_csv_corrida(csv)
        base = nombre_lote or nombre_desde_corrida(csv, prefijo)
        if parte is None:
            disponibles = partes_de_corrida(csv)
            if len(disponibles) > 1:
                raise ErrorDeCorrida(
                    f"La corrida esta repartida en {len(disponibles)} partes; "
                    f"cada una es un lote distinto."
                )
            parte = disponibles[0] if disponibles else None

        if parte is not None:
            registros = registros_desde_entrega(
                filas, parte.paginas, resolutor
            )
            detalle_orden = (
                f"{sum(1 for r in registros if r.es_separador)} separadores"
            )
        else:
            # Corridas exportadas antes de que existiera el indice. Se sigue
            # el orden del CSV, que solo coincide con el lote si el PDF no
            # llevaba ningun separador; si llevaba, la guarda de cantidad lo
            # para antes de escribir nada.
            logger.warning(
                "La corrida no tiene indice de paginas; se asume que el PDF "
                "no lleva separadores"
            )
            registros = registros_desde_csv(filas, resolutor)
            detalle_orden = "sin indice de paginas"
        if not registros:
            raise ErrorDeCorrida(
                "El CSV de la corrida no tiene ninguna bitacora utilizable"
            )
        carpeta = Path(carpeta)
        manifiesto = Manifiesto(
            job_id=carpeta.name,
            nombre_batch=parte.nombre_lote(base) if parte else base,
            repo_id=config.repo_id,
            csv_origen=str(Path(csv).resolve()),
            pdf_origen=str(parte.pdf) if parte else "",
            parte=parte.indice if parte else 1,
            partes=parte.total if parte else 1,
            paginas_por_batch=int(paginas_por_batch),
            solo_subir=bool(parte and parte.revisar),
            doc_type=config.doc_type,
            audit_status=config.audit_status,
            registros=registros,
        )
        manifiesto.etapa("procesar").marcar(EstadoEtapa.HECHA, str(csv))
        manifiesto.etapa("preparar").marcar(
            EstadoEtapa.HECHA,
            f"{len(manifiesto.bitacoras())} bitacoras, {detalle_orden}",
        )
        trabajo = cls(config, carpeta, manifiesto)
        trabajo.guardar()
        return trabajo

    @classmethod
    def cargar(cls, config: AirVaultConfig,
               carpeta: Path | str) -> "Trabajo":
        return cls(config, Path(carpeta), manifiestos.cargar(carpeta))

    @classmethod
    def abrir_o_preparar(
        cls, config: AirVaultConfig, carpeta: Path | str, csv: Path | str,
        nombre_lote: str = "", prefijo: str = PREFIJO_POR_DEFECTO,
        resolutor: Optional[ResolutorFlota] = None,
        parte: Optional[ParteDeEntrega] = None,
        paginas_por_batch: int = PAGINAS_POR_BATCH_POR_DEFECTO,
    ) -> "Trabajo":
        """Retoma el trabajo si ya existe para este CSV; si no, lo crea.

        Es lo que hace que apretar el boton dos veces no empiece de cero:
        un trabajo a medias conserva que paginas ya se escribieron. Si la
        carpeta guarda un trabajo de otra corrida, se rehace: seguir con el
        anterior escribiria los datos de una corrida en el lote de otra.
        """
        carpeta = Path(carpeta)
        if manifiestos.existe(carpeta):
            trabajo = cls.cargar(config, carpeta)
            mismo_csv = (
                Path(trabajo.manifiesto.csv_origen or "") ==
                Path(csv).resolve()
            )
            mismo_pdf = (
                parte is None
                or Path(trabajo.manifiesto.pdf_origen) == parte.pdf
            )
            mismo_limite = trabajo.manifiesto.paginas_por_batch in (
                0, int(paginas_por_batch)
            )
            if mismo_csv and mismo_pdf and mismo_limite:
                propuesto = (
                    parte.nombre_lote(nombre_lote) if parte and nombre_lote
                    else nombre_lote
                )
                if propuesto:
                    trabajo.manifiesto.nombre_batch = propuesto
                    trabajo.guardar()
                return trabajo
            logger.info(
                "El trabajo {} era de otra corrida; se rehace", carpeta.name
            )
        return cls.preparar(
            config, carpeta, csv, nombre_lote, prefijo, resolutor, parte,
            paginas_por_batch,
        )

    def guardar(self) -> Path:
        return manifiestos.guardar(self.manifiesto, self.carpeta)

    # ── etapas ─────────────────────────────────────────────────────

    def subir(self, sesion, pdf: Path | str = "",
              avisar: Optional[Aviso] = None, cliente=None) -> None:
        """Sube el PDF de la corrida por Quick Upload.

        Se salta sola si el lote ya se subio en un intento anterior: volver
        a subirlo crearia un segundo lote y no habria forma de saber en
        cual escribir.

        Con ``cliente`` se anota antes que lotes habia en la cola. Es lo
        unico con lo que despues se reconoce el propio: Quick Upload no
        admite nombre de lote y todos llegan como «Empty-Batch».
        """
        from app.airvault.uploader import SubidorQuickUpload

        if self.manifiesto.etapa_hecha("subir"):
            logger.info("El lote ya estaba subido; no se vuelve a subir")
            return
        archivo = Path(pdf or self.manifiesto.pdf_origen)
        if not archivo.is_file():
            raise ErrorDeCorrida(
                f"No esta el archivo de entrega {archivo.name}"
            )
        # De la primera bitacora, no de la primera pagina: la primera
        # suele ser un separador, sin avion, y Aircraft es obligatorio en
        # Quick Upload. Estos valores son solo la clasificacion inicial del
        # archivo; lo de cada pagina lo escribe el indexado despues.
        bitacoras = self.manifiesto.bitacoras()
        primera = bitacoras[0] if bitacoras else self.manifiesto.registros[0]
        valores = valores_de_indice(
            primera, self.manifiesto.doc_type,
            self.manifiesto.audit_status, self.manifiesto.nombre_batch,
        )
        if cliente is not None:
            self.anotar_lotes_previos(cliente)
        subidor = SubidorQuickUpload(sesion, self.manifiesto.repo_id)
        self.manifiesto.etapa("subir").marcar(EstadoEtapa.EN_CURSO)
        self.guardar()
        resultado = subidor.subir(archivo, valores, avisar=avisar)
        if not resultado.ok:
            self.manifiesto.etapa("subir").marcar(
                EstadoEtapa.ERROR, resultado.detalle
            )
            self.guardar()
            raise ErrorDeCorrida(
                f"No se pudo subir {archivo.name}: {resultado.detalle}"
            )
        self.manifiesto.etapa("subir").marcar(EstadoEtapa.HECHA, archivo.name)
        self.guardar()

    def anotar_lotes_previos(self, cliente) -> None:
        """Guarda la cola tal como esta antes de subir.

        Si no se puede leer no se corta la subida: se pierde el atajo para
        reconocer el lote, no el trabajo.
        """
        try:
            previos = [lote.batch_id for lote in cliente.listar_lotes()]
        except Exception as exc:  # noqa: BLE001 - la subida sigue igual
            logger.info("No se pudo anotar la cola antes de subir: {}", exc)
            return
        self.manifiesto.lotes_previos = previos
        logger.debug("En la cola habia {} lotes antes de subir", len(previos))

    def omitir_subida(self, motivo: str = "subido a mano") -> None:
        """Marca la subida como hecha por fuera del programa."""
        self.manifiesto.etapa("subir").marcar(EstadoEtapa.OMITIDA, motivo)
        self.guardar()

    def descubrir(
        self, cliente, esperar: bool = True,
        dormir: Callable[[float], None] = time.sleep,
        avisar: Optional[Aviso] = None,
    ) -> str:
        """Ubica el lote en AirVault por su nombre y lo deja anotado.

        Un lote recien subido tarda en cruzar el procesamiento del servidor,
        asi que no aparecer todavia no es un error hasta que vence el limite
        de espera.
        """
        esperadas = len(self.manifiesto.registros)
        nombre = self.manifiesto.nombre_batch
        if avisar is not None:
            avisar(f"Buscando el lote {nombre} en AirVault", 0, 0)
        previos = self.manifiesto.lotes_previos or None
        if esperar:
            lote = esperar_lote(
                cliente.listar_lotes, nombre, self.manifiesto.repo_id,
                esperadas, self.config.espera_descubrimiento_s,
                self.config.espera_maxima_s, dormir=dormir,
                previos=previos,
            )
        else:
            lotes = cliente.listar_lotes()
            try:
                lote = buscar_lote(
                    lotes, nombre, self.manifiesto.repo_id, esperadas,
                )
            except LoteNoEncontrado:
                lote = previos is not None and buscar_lote_nuevo(
                    lotes, previos, self.manifiesto.repo_id, esperadas,
                )
                if not lote:
                    raise
        return self.anotar_lote(cliente, lote, avisar)

    def anotar_lote(self, cliente, lote: ResumenLote,
                    avisar: Optional[Aviso] = None) -> str:
        """Da por propio el lote encontrado y le pone su nombre.

        Esta aparte de :meth:`descubrir` porque la comprobacion periodica
        ubica lotes sin esperar a nada, y encontrarlo tiene que dejar el
        trabajo igual de anotado se haya llegado por donde se haya llegado.
        """
        self.manifiesto.batch_id = lote.batch_id
        self._ponerle_nombre(cliente, lote, avisar)
        self.manifiesto.etapa("descubrir").marcar(
            EstadoEtapa.HECHA, f"{lote.batch_id} ({lote.paginas} paginas)"
        )
        self.guardar()
        return lote.batch_id

    def _ponerle_nombre(self, cliente, lote, avisar: Optional[Aviso] = None
                        ) -> None:
        """Deja el lote con su nombre en la cola de AirVault.

        Quick Upload no admite ninguno y todo lo que sube el programa llega
        como «Empty-Batch», que en la pantalla no distingue un lote de
        otro. Se le pone aqui, en cuanto se sabe cual es.
        """
        nombre = self.manifiesto.nombre_batch
        if not nombre or normalizar_nombre(lote.nombre) == normalizar_nombre(nombre):
            return
        renombrar = getattr(cliente, "renombrar_lote", None)
        if renombrar is None:
            return
        if avisar is not None:
            avisar(f"Nombrando el lote {nombre}", 0, 0)
        renombrar(lote.batch_id, nombre)

    def fijar_lote(self, batch_id: str) -> None:
        """Salta la busqueda y apunta el lote a mano."""
        self.manifiesto.batch_id = str(batch_id).strip()
        self.manifiesto.etapa("descubrir").marcar(
            EstadoEtapa.HECHA, f"fijado a mano: {self.manifiesto.batch_id}"
        )
        self.guardar()

    def planificar(
        self, cliente, resolutor: Optional[ResolutorFlota] = None,
        sobrescribir: bool = False, avisar: Optional[Aviso] = None,
    ) -> Tuple[Plan, Indexador]:
        """Calcula el plan completo sin escribir nada.

        Es el mismo camino que sigue la escritura, asi que lo que muestra el
        reporte es lo que se va a enviar y no una version resumida.
        """
        if not self.manifiesto.batch_id:
            raise ErrorDeCorrida(
                "El trabajo todavia no tiene lote. Hay que buscarlo primero."
            )
        info = self._abrir_lote(cliente)
        paginas = paginas_de_lote(info)
        try:
            picklist = cliente.picklist_matriculas()
        except Exception as exc:  # noqa: BLE001 - el catalogo no es critico
            logger.warning("No se pudo leer el picklist de matriculas: {}", exc)
            picklist = []
        if avisar is not None:
            avisar("Leyendo las paginas del lote", 0, paginas)
        def persistir(_manifiesto: Manifiesto) -> None:
            self.guardar()

        indexador = Indexador(
            cliente, self.manifiesto, picklist, sobrescribir,
            al_guardar=persistir,
            resolutor=resolutor or ResolutorFlota(),
        )
        try:
            plan = indexador.planificar(paginas)
        finally:
            # Planificar solo lee. Antes el lote se quedaba tomado hasta
            # que alguien pulsara «Indexar», y entre una cosa y otra puede
            # pasar un rato largo o no pulsarse nunca: AirVault admite un
            # solo dueno, asi que mientras tanto nadie mas podia abrirlo,
            # ni la persona que lo revisa ni el propio programa al volver.
            # Escribir lo vuelve a tomar, que es una peticion al lado de
            # las cientos que hace el indexado.
            self.cerrar(cliente)
        self.guardar()
        return plan, indexador

    def tomar(self, cliente):
        """Toma el lote para escribirlo, y dice quien lo tiene si no se deja.

        Escribir una pagina exige ser el dueno del lote. Se toma justo
        antes de escribir y se suelta al terminar, en vez de quedarselo
        desde la revision: un lote tomado no da error, deja colgada la
        siguiente apertura.
        """
        return self._abrir_lote(cliente)

    def _abrir_lote(self, cliente):
        """Toma el lote y, si no contesta, dice quien lo tiene tomado.

        AirVault admite un solo dueno por lote y no responde «esta
        ocupado»: deja la peticion colgada hasta que vence el tiempo
        limite. El listado si dice quien lo tiene, asi que se pregunta
        justo cuando hace falta y el mensaje deja de ser un tiempo agotado
        sin explicacion.
        """
        from app.airvault.session import ErrorDeConexion

        try:
            info = cliente.abrir_lote(self.manifiesto.batch_id)
        except ErrorDeConexion as exc:
            dueno = self._quien_lo_tiene(cliente)
            if not dueno:
                raise
            raise ErrorDeConexion(
                f"El lote {self.manifiesto.nombre_batch} esta abierto por "
                f"{dueno} y AirVault no lo entrega a nadie mas: la peticion "
                f"se queda esperando sin contestar. Hay que cerrarlo en "
                f"AirVault —abrirlo y salir con Close— y volver a intentar."
            ) from exc
        self._tomado = True
        return info

    def _quien_lo_tiene(self, cliente) -> str:
        """Usuario que tiene tomado el lote, o vacio si no se pudo saber."""
        try:
            lotes = cliente.listar_lotes(self.manifiesto.nombre_batch)
        except Exception as exc:  # noqa: BLE001 - es solo para el mensaje
            logger.debug("No se pudo averiguar quien tiene el lote: {}", exc)
            return ""
        for lote in lotes or []:
            if lote.batch_id == self.manifiesto.batch_id:
                return lote.bloqueado_por
        return ""

    def cerrar(self, cliente) -> None:
        """Suelta el lote en AirVault. No levanta: es limpieza.

        Se llama al terminar y tambien cuando algo se corta a medias. Un
        lote que queda tomado no da error: cuelga la siguiente apertura,
        que es mucho peor de diagnosticar.

        Soltar dos veces no se intenta. El lote de Revisar ya se suelta al
        planificarlo —nadie va a escribir en el—, y volver a pedirlo hacia
        que AirVault contestara «Batch no esta tomado por este usuario» con
        un 500, que ademas se reintentaba tres veces y terminaba en un
        aviso que hacia pensar que el lote habia quedado colgado.
        """
        batch_id = self.manifiesto.batch_id
        if not batch_id or not self._tomado:
            return
        self._tomado = False
        try:
            cliente.cerrar_lote(batch_id)
        except Exception as exc:  # noqa: BLE001 - cerrar nunca tumba nada
            logger.warning(
                "No se pudo soltar el lote {} en AirVault: {}. Si la "
                "siguiente apertura se queda esperando, hay que cerrarlo "
                "alli a mano.",
                batch_id, exc,
            )
        else:
            logger.info("Lote {} soltado en AirVault", batch_id)

    def indexar(
        self, indexador: Indexador, plan: Plan,
        detener_en_error: bool = True, avisar: Optional[Aviso] = None,
    ) -> Resultado:
        """Escribe las paginas del plan que quedaron habilitadas.

        Toma el lote antes de escribir y lo suelta al terminar. La revision
        ya no se lo queda: entre revisar y escribir puede pasar un rato
        largo, y AirVault admite un solo dueno.
        """
        self.manifiesto.etapa("indexar").marcar(EstadoEtapa.EN_CURSO)
        self.guardar()
        avanzar = None
        if avisar is not None:
            def avanzar(hechas: int, previstas: int) -> None:
                avisar("Escribiendo en AirVault", hechas, previstas)
        if plan.escribibles or (
            plan.separadores and not self.manifiesto.solo_subir
        ):
            self.tomar(indexador.cliente)
        try:
            resultado = indexador.aplicar(plan, detener_en_error, avanzar)
        finally:
            # Tambien si se corto a medias: lo escrito queda escrito y el
            # lote no se queda bloqueado por un trabajo que ya no corre.
            self.cerrar(indexador.cliente)
        con_error = bool(
            resultado.fallidas or resultado.separadores_pendientes
            or resultado.interrumpido
        )
        detalle = (
            f"escritas {resultado.escritas}, omitidas {resultado.omitidas}, "
            f"fallidas {resultado.fallidas}, separadores borrados "
            f"{resultado.separadores_borrados}"
        )
        if resultado.separadores_pendientes:
            detalle += (
                f", separadores sin borrar "
                f"{resultado.separadores_pendientes}"
            )
        self.manifiesto.etapa("indexar").marcar(
            EstadoEtapa.ERROR if con_error else EstadoEtapa.HECHA, detalle
        )
        self.guardar()
        return resultado

    def verificar(self, cliente) -> Tuple[int, int, Sequence[str]]:
        """Relee el lote y confirma contra el servidor como quedo."""
        validas, total, problemas = verificar_lote(cliente, self.manifiesto)
        self.manifiesto.etapa("verificar").marcar(
            EstadoEtapa.HECHA if validas == total else EstadoEtapa.ERROR,
            f"{validas}/{total} en Valid",
        )
        self.guardar()
        return validas, total, problemas

    def completar(self, cliente) -> "ResultadoCompletar":
        """Da el lote por terminado y lo saca de la cola del Web Index.

        AirVault solo lo acepta con **todas** las paginas en verde: basta
        una a la que le falte un campo obligatorio —casi siempre la fecha—
        para que no deje cerrar el lote. Asi que primero se miran las
        paginas y, si alguna bloquea, no se intenta: se dice cuales son y
        el lote se queda en la cola, que es justo donde tiene que quedarse
        para que alguien las arregle.

        La misma comprobacion que hace la pantalla antes de habilitar su
        boton «Complete»: cuenta la pagina que encabeza cada documento,
        salvo las borradas.

        Las paginas separadoras del PDF —la matricula de cada grupo,
        «REVISAR», «POSIBLES DISCREPANCIAS»— tambien cuentan, y nunca van a
        estar en verde: no son bitacoras, no tienen fecha ni avion que
        escribirles. Como no son documentos, se quitan del lote antes de
        cerrarlo, que es lo mismo que hace a mano quien indexa. Se quitan
        solo si con eso el lote queda cerrable: si ademas hay una bitacora
        en amarillo el lote no se cierra hoy, y entonces mas vale no
        haberlo tocado.
        """
        if self.manifiesto.solo_subir:
            return ResultadoCompletar(
                False, [], len(self.manifiesto.registros),
                "el lote REVISAR se conserva para indexarlo a mano",
            )
        batch_id = self.manifiesto.batch_id or ""
        if not batch_id:
            raise ErrorDeCorrida(
                "El trabajo todavia no tiene lote; no hay nada que terminar."
            )
        paginas = list(cliente.paginas_del_lote(batch_id))
        bloqueadas = [
            p.pagina for p in paginas
            if not p.borrada and p.encabeza_documento and not p.valida
        ]
        separadores = {r.seq for r in self.manifiesto.separadores()}
        quitables = [n for n in bloqueadas if n in separadores]
        bloqueadas = [n for n in bloqueadas if n not in separadores]
        if bloqueadas:
            detalle = (
                f"{len(bloqueadas)} de {len(paginas)} paginas no estan en "
                f"verde ({_enumerar(bloqueadas)}); AirVault no deja cerrar "
                f"el lote hasta que se completen"
            )
            self.manifiesto.etapa("completar").marcar(
                EstadoEtapa.OMITIDA, detalle
            )
            self.guardar()
            logger.info("El lote {} no se cierra: {}", batch_id, detalle)
            return ResultadoCompletar(False, bloqueadas, len(paginas), detalle)
        self.tomar(cliente)
        quitadas: List[int] = []
        try:
            for numero in quitables:
                if cliente.borrar_pagina(batch_id, numero, True):
                    quitadas.append(numero)
            faltan = [n for n in quitables if n not in quitadas]
            if faltan:
                # Quitar paginas pide un permiso aparte. Sin el, el lote se
                # queda como estaba y hay que sacarlas en AirVault a mano.
                detalle = (
                    f"quedaron {len(faltan)} paginas separadoras en el lote "
                    f"({_enumerar(faltan)}) y AirVault no deja cerrarlo con "
                    f"ellas; hace falta el permiso «Delete Batch Image»"
                )
                self.cerrar(cliente)
                self.manifiesto.etapa("completar").marcar(
                    EstadoEtapa.OMITIDA, detalle
                )
                self.guardar()
                logger.info("El lote {} no se cierra: {}", batch_id, detalle)
                return ResultadoCompletar(
                    False, faltan, len(paginas), detalle, quitadas
                )
            cliente.completar_lote(batch_id)
        except BaseException:
            self.cerrar(cliente)
            raise
        # Terminado, el lote sale de la cola del Web Index: soltarlo seria
        # pedirle a AirVault que suelte algo que ya no esta ahi.
        self._tomado = False
        detalle = f"{len(paginas) - len(quitadas)} paginas en verde"
        if quitadas:
            detalle += f", {len(quitadas)} separadores quitados del lote"
        self.manifiesto.etapa("completar").marcar(EstadoEtapa.HECHA, detalle)
        self.guardar()
        logger.info("Lote {} dado por terminado en AirVault", batch_id)
        return ResultadoCompletar(True, [], len(paginas), detalle, quitadas)


# ── la corrida entera, parte por parte ─────────────────────────────

def carpeta_de_parte(carpeta: Path | str, parte: ParteDeEntrega) -> Path:
    """Carpeta del trabajo de una parte dentro del trabajo de la corrida.

    Con una sola parte se usa la carpeta tal cual, que es donde han vivido
    siempre los trabajos de una corrida sin repartir.
    """
    carpeta = Path(carpeta)
    if parte.revisar:
        if parte.total <= 1:
            return carpeta / "revisar"
        return carpeta / f"revisar-{parte.indice:02d}"
    if parte.total <= 1:
        return carpeta
    return carpeta / f"parte-{parte.indice:02d}"


def preparar_partes(
    config: AirVaultConfig, carpeta: Path | str, csv: Path | str,
    nombre_lote: str = "", prefijo: str = PREFIJO_POR_DEFECTO,
    resolutor: Optional[ResolutorFlota] = None,
    paginas_por_batch: int = PAGINAS_POR_BATCH_POR_DEFECTO,
    avisar: Optional[Aviso] = None,
) -> List["Trabajo"]:
    """Un trabajo por cada archivo de entrega de la corrida.

    Cada parte es un lote distinto en AirVault, con su nombre, su
    manifiesto y sus guardas. Repartirlas asi es lo que deja que una parte
    se caiga o se retome sin arrastrar a las demas.
    """
    partes = partes_para_airvault(
        comprobar_entrega(csv), carpeta, paginas_por_batch, avisar
    )
    resolutor = resolutor or ResolutorFlota()
    return [
        Trabajo.abrir_o_preparar(
            config, carpeta_de_parte(carpeta, parte), csv,
            nombre_lote, prefijo, resolutor, parte, paginas_por_batch,
        )
        for parte in partes
    ]


def _prefijo(trabajo: "Trabajo") -> str:
    """Como se nombra una parte en los avisos de avance."""
    manifiesto = trabajo.manifiesto
    if manifiesto.solo_subir:
        return "Revisar: "
    if manifiesto.partes <= 1:
        return ""
    return f"Parte {manifiesto.parte} de {manifiesto.partes}: "


def subir_partes(
    trabajos: Sequence["Trabajo"], sesion, avisar: Optional[Aviso] = None,
    cliente=None, dormir: Callable[[float], None] = time.sleep,
) -> None:
    """Sube primero todos los archivos, sin esperar ni descubrir entre ellos.

    La espera de procesamiento empieza unicamente cuando todas las cargas
    terminaron. La comprobacion conjunta reconoce despues cada batch nuevo
    y evita que dos trabajos reclamen el mismo resultado de Quick Upload.
    """
    for trabajo in trabajos:
        cabeza = _prefijo(trabajo)

        def propio(texto: str, hechas: int, total: int,
                   cabeza: str = cabeza) -> None:
            if avisar is not None:
                avisar(f"{cabeza}{texto}", hechas, total)

        trabajo.subir(sesion, avisar=propio if avisar else None,
                      cliente=cliente)


def subir_y_descubrir_partes(
    trabajos: Sequence["Trabajo"], sesion, cliente, esperar: bool = True,
    dormir: Callable[[float], None] = time.sleep,
    avisar: Optional[Aviso] = None,
) -> None:
    """Sube cada parte y espera a su lote antes de mandar la siguiente.

    De una en una y no todas de golpe: AirVault junta en un mismo lote los
    archivos que le llegan seguidos —comprobado subiendo la entrega y la
    parte de Revisar una detras de otra: quedaron los dos en un solo lote
    de 33 paginas—, y dos partes en el mismo lote no se pueden indexar por
    separado, que es justo para lo que se reparten. Esperar a que la
    anterior aparezca en la cola las mantiene aparte, y de paso la lista de
    lotes que se anota antes de subir queda exacta.
    """
    for trabajo in trabajos:
        cabeza = _prefijo(trabajo)

        def propio(texto: str, hechas: int, total: int,
                   cabeza: str = cabeza) -> None:
            if avisar is not None:
                avisar(f"{cabeza}{texto}", hechas, total)

        trabajo.subir(sesion, avisar=propio if avisar else None,
                      cliente=cliente)
        trabajo.descubrir(cliente, esperar, dormir,
                          propio if avisar else None)


def descubrir_partes(
    trabajos: Sequence["Trabajo"], cliente, esperar: bool = True,
    dormir: Callable[[float], None] = time.sleep,
    avisar: Optional[Aviso] = None,
) -> None:
    """Ubica en AirVault el lote de cada parte."""
    for trabajo in trabajos:
        cabeza = _prefijo(trabajo)

        def propio(texto: str, hechas: int, total: int,
                   cabeza: str = cabeza) -> None:
            if avisar is not None:
                avisar(f"{cabeza}{texto}", hechas, total)

        trabajo.descubrir(cliente, esperar, dormir,
                          propio if avisar else None)


def cargar_partes(
    config: AirVaultConfig, carpeta: Path | str, csv: Path | str,
) -> List["Trabajo"]:
    """Trabajos ya preparados de una ejecucion, sin volver a prepararlos.

    Es lo que permite retomar una ejecucion subida ayer: los manifiestos
    dicen en que quedo cada parte, asi que la ventana puede enseñar sus
    lotes sin tocar la red ni volver a escribir nada en el disco.

    Devuelve la lista vacia si falta el manifiesto de alguna parte: media
    ejecucion cargada seria peor que ninguna, porque las partes que
    faltaran parecerian no existir.
    """
    partes_originales = partes_de_corrida(csv)
    if not partes_originales:
        return []
    carpeta = Path(carpeta)
    carpetas = [carpeta] if manifiestos.existe(carpeta) else []
    if carpeta.is_dir():
        carpetas.extend(
            hija for hija in sorted(carpeta.iterdir())
            if hija.is_dir() and manifiestos.existe(hija)
        )
    objetivo = Path(csv).resolve()
    trabajos: List["Trabajo"] = []
    for propia in carpetas:
        trabajo = Trabajo.cargar(config, propia)
        if Path(trabajo.manifiesto.csv_origen or "") != objetivo:
            continue
        if not Path(trabajo.manifiesto.pdf_origen or "").is_file():
            return []
        trabajos.append(trabajo)
    if not trabajos:
        return []

    def grupo(revisar: bool) -> Optional[List["Trabajo"]]:
        suyos = [t for t in trabajos if t.manifiesto.solo_subir is revisar]
        if not suyos:
            return []
        if (
            revisar and len(suyos) == 1
            and suyos[0].manifiesto.paginas_por_batch == 0
        ):
            # Antes del limite configurable, el unico REVISAR heredaba la
            # numeracion de los automaticos. Se acepta para poder retomar
            # trabajos ya subidos con ese formato antiguo.
            return suyos
        esperadas = suyos[0].manifiesto.partes
        numeros = {t.manifiesto.parte for t in suyos}
        if (
            any(t.manifiesto.partes != esperadas for t in suyos)
            or numeros != set(range(1, esperadas + 1))
        ):
            return None
        return sorted(suyos, key=lambda t: t.manifiesto.parte)

    automaticos = grupo(False)
    revisar = grupo(True)
    if automaticos is None or revisar is None:
        return []
    habia_automaticos = any(not parte.revisar for parte in partes_originales)
    habia_revisar = any(parte.revisar for parte in partes_originales)
    if (
        habia_automaticos != bool(automaticos)
        or habia_revisar != bool(revisar)
    ):
        # Una preparacion cortada no se presenta como una entrega completa.
        return []
    return list(automaticos) + list(revisar)


def cargar_trabajos_pendientes(
    config: AirVaultConfig, carpeta_raiz: Path | str,
) -> List["Trabajo"]:
    """Recupera batches creados por la aplicacion que aun requieren trabajo.

    Solo se confia en manifiestos propios. Los batches de REVISAR se dejan
    fuera porque su indexado es deliberadamente manual, y los completados ya
    salieron de la cola de Web Index.
    """
    raiz = Path(carpeta_raiz)
    if not raiz.is_dir():
        return []
    trabajos: List["Trabajo"] = []
    for ruta in sorted(raiz.rglob(manifiestos.MANIFIESTO_FILENAME)):
        try:
            trabajo = Trabajo.cargar(config, ruta.parent)
        except (OSError, ValueError):
            continue
        manifiesto = trabajo.manifiesto
        subida = manifiesto.etapas.get("subir")
        completar = manifiesto.etapas.get("completar")
        if manifiesto.solo_subir or not subida:
            continue
        if subida.estado not in (
            EstadoEtapa.HECHA, EstadoEtapa.OMITIDA, EstadoEtapa.EN_CURSO,
        ):
            continue
        if completar and completar.estado is EstadoEtapa.HECHA:
            continue
        trabajos.append(trabajo)
    return sorted(
        trabajos,
        key=lambda t: (t.manifiesto.creado, str(t.carpeta).casefold()),
    )


def reiniciar_trabajos_incompletos(
    trabajos: Sequence["Trabajo"],
) -> List[Tuple["Trabajo", str]]:
    """Reinicia solo el primer paso local que no termino en cada trabajo.

    No borra paginas ni batches en AirVault. Al reiniciar indexado, la nueva
    planificacion vuelve a leer el servidor: conserva las paginas Valid y
    reenvia unicamente las que sigan amarillas o sin terminar.
    """
    reiniciados: List[Tuple["Trabajo", str]] = []
    for trabajo in trabajos:
        manifiesto = trabajo.manifiesto
        subida = manifiesto.etapas.get("subir")
        subida_hecha = bool(subida and subida.estado in (
            EstadoEtapa.HECHA, EstadoEtapa.OMITIDA,
        ))
        if not subida_hecha:
            manifiesto.batch_id = None
            manifiesto.etapas["subir"] = Etapa()
            manifiesto.etapas["descubrir"] = Etapa()
            manifiesto.etapas["indexar"] = Etapa()
            manifiesto.etapas["verificar"] = Etapa()
            manifiesto.etapas["completar"] = Etapa()
            reiniciados.append((trabajo, "subir"))
        else:
            verificada = manifiesto.etapas.get("verificar")
            if not verificada or verificada.estado is not EstadoEtapa.HECHA:
                manifiesto.etapas["indexar"] = Etapa()
                manifiesto.etapas["verificar"] = Etapa()
                manifiesto.etapas["completar"] = Etapa()
                for registro in manifiesto.registros:
                    if registro.es_separador:
                        continue
                    registro.estado = EstadoRegistro.PENDIENTE
                    registro.avisos = []
                reiniciados.append((trabajo, "indexar"))
            else:
                completar = manifiesto.etapas.get("completar")
                if completar and completar.estado is not EstadoEtapa.HECHA:
                    manifiesto.etapas["completar"] = Etapa()
                    reiniciados.append((trabajo, "completar"))
        trabajo.guardar()
    return reiniciados


def estado_local(trabajo: "Trabajo") -> EstadoParte:
    """En que va una parte segun su manifiesto, sin preguntar a AirVault.

    Sirve para pintar la lista en cuanto se elige una ejecucion. Lo que no
    puede saber es si el servidor ya termino de procesar el lote: eso solo
    lo dice :func:`comprobar_partes`, que si pregunta.
    """
    manifiesto = trabajo.manifiesto
    completar = manifiesto.etapas.get("completar")
    if completar and completar.estado is EstadoEtapa.HECHA:
        return EstadoParte(trabajo, COMPLETADO, "cerrado en AirVault")
    if not manifiesto.etapa_hecha("subir"):
        return EstadoParte(trabajo, SIN_SUBIR, "todavia sin subir")
    verificar = manifiesto.etapas.get("verificar")
    if verificar and verificar.estado is EstadoEtapa.HECHA:
        return EstadoParte(
            trabajo, INDEXADO, verificar.detalle
        )
    if manifiesto.solo_subir:
        return EstadoParte(trabajo, SOLO_REVISAR, "subido, se indexa a mano")
    return EstadoParte(trabajo, BUSCANDO, "subido; falta comprobar")


def _ubicar(trabajo: "Trabajo", cliente,
            lotes: Sequence[ResumenLote]) -> Optional[ResumenLote]:
    """El lote de esta parte en la cola, buscandolo si aun no se sabia.

    Devuelve ``None`` mientras AirVault no lo haya sacado, que no es un
    fallo: un lote recien subido tarda en cruzar su procesamiento.
    """
    manifiesto = trabajo.manifiesto
    if manifiesto.batch_id:
        return buscar_por_id(lotes, manifiesto.batch_id)
    esperadas = len(manifiesto.registros)
    try:
        lote = buscar_lote(
            lotes, manifiesto.nombre_batch, manifiesto.repo_id, esperadas
        )
    except LoteNoEncontrado:
        previos = manifiesto.lotes_previos
        lote = buscar_lote_nuevo(
            lotes, previos, manifiesto.repo_id, esperadas
        ) if previos else None
    if lote is None:
        return None
    trabajo.anotar_lote(cliente, lote)
    return lote


def _estado_de(trabajo: "Trabajo", cliente,
               lotes: Sequence[ResumenLote]) -> EstadoParte:
    """En que va una parte, mirando la cola que se acaba de pedir."""
    manifiesto = trabajo.manifiesto
    esperadas = len(manifiesto.registros)
    completar = manifiesto.etapas.get("completar")
    if completar and completar.estado is EstadoEtapa.HECHA:
        return EstadoParte(trabajo, COMPLETADO, "cerrado en AirVault")
    subida = manifiesto.etapas.get("subir")
    subida_rastreable = bool(subida and subida.estado in (
        EstadoEtapa.HECHA, EstadoEtapa.OMITIDA, EstadoEtapa.EN_CURSO,
    ))
    if not subida_rastreable:
        return EstadoParte(trabajo, SIN_SUBIR, "todavia sin subir")
    if not manifiesto.batch_id and manifiesto.lotes_previos:
        return EstadoParte(
            trabajo, BUSCANDO,
            "esperando que aparezca el conjunto completo de batches",
        )
    lote = _ubicar(trabajo, cliente, lotes)
    if lote is None:
        return EstadoParte(
            trabajo, BUSCANDO, "AirVault todavia no lo saca en la cola"
        )
    if lote.paginas != esperadas:
        # Aparece en la cola antes de estar entero. Escribir asi correria
        # cada dato a la bitacora de al lado, asi que hasta que las
        # paginas cuadren la parte no esta lista.
        return EstadoParte(
            trabajo, PROCESANDO,
            f"{lote.paginas} de {esperadas} paginas", lote,
        )
    if manifiesto.solo_subir:
        return EstadoParte(
            trabajo, SOLO_REVISAR, f"{esperadas} paginas sin avion", lote
        )
    verificar = manifiesto.etapas.get("verificar")
    if verificar and verificar.estado is EstadoEtapa.HECHA:
        return EstadoParte(
            trabajo, INDEXADO, verificar.detalle, lote
        )
    if lote.bloqueado_por:
        # Tomado por alguien, AirVault no lo entrega: abrirlo dejaria la
        # peticion colgada hasta que venza el tiempo limite.
        return EstadoParte(
            trabajo, TOMADO, f"lo tiene abierto {lote.bloqueado_por}", lote
        )
    return EstadoParte(trabajo, LISTO, f"{esperadas} paginas", lote)


def _asignar_batches_nuevos(
    trabajos: Sequence["Trabajo"], cliente, lotes: Sequence[ResumenLote],
) -> None:
    """Asigna de una vez los resultados de varias cargas consecutivas.

    Todos los Quick Upload se envian antes de esperar. Por eso varios
    manifiestos pueden compartir la misma foto de la cola anterior y la
    busqueda individual seria ambigua. Esta funcion espera a que aparezca el
    conjunto completo y lo empareja por cantidad de paginas y orden de
    recepcion, sin permitir que un batch se asigne dos veces.
    """
    reclamados = {
        t.manifiesto.batch_id.strip().upper()
        for t in trabajos if t.manifiesto.batch_id
    }
    pendientes: List["Trabajo"] = []
    for trabajo in trabajos:
        manifiesto = trabajo.manifiesto
        if manifiesto.batch_id:
            continue
        subida = manifiesto.etapas.get("subir")
        if not subida or subida.estado not in (
            EstadoEtapa.HECHA, EstadoEtapa.OMITIDA, EstadoEtapa.EN_CURSO,
        ):
            continue
        try:
            candidato = buscar_lote(
                lotes, manifiesto.nombre_batch, manifiesto.repo_id,
                len(manifiesto.registros),
            )
        except (LoteNoEncontrado, LoteAmbiguo):
            pendientes.append(trabajo)
            continue
        clave = candidato.batch_id.strip().upper()
        if clave in reclamados:
            pendientes.append(trabajo)
            continue
        if subida.estado is EstadoEtapa.EN_CURSO:
            subida.marcar(EstadoEtapa.HECHA, "recuperado en AirVault")
        trabajo.anotar_lote(cliente, candidato)
        reclamados.add(clave)

    grupos: dict[tuple[int, tuple[str, ...]], List["Trabajo"]] = {}
    for trabajo in pendientes:
        manifiesto = trabajo.manifiesto
        if not manifiesto.lotes_previos:
            continue
        clave = (
            manifiesto.repo_id,
            tuple(str(x).strip().upper() for x in manifiesto.lotes_previos),
        )
        grupos.setdefault(clave, []).append(trabajo)

    # Las fotos mas recientes primero: si un batch alcanzo a aparecer entre
    # dos uploads, el manifiesto siguiente lo incluye entre sus previos. Al
    # reclamar primero ese resultado mas nuevo, el anterior deja de ser
    # ambiguo sin necesidad de esperar ni listar durante la carga.
    grupos_ordenados = sorted(
        grupos.items(), key=lambda item: len(item[0][1]), reverse=True
    )
    for (repo_id, previos), grupo in grupos_ordenados:
        nuevos = [
            lote for lote in recien_llegados(lotes, previos, repo_id)
            if lote.batch_id.strip().upper() not in reclamados
        ]
        esperadas = Counter(len(t.manifiesto.registros) for t in grupo)
        if len(nuevos) != len(grupo):
            continue
        # Si AirVault ya termino, las cantidades dan un emparejamiento mas
        # fuerte. Mientras aun procesa pueden ser menores; con el conjunto
        # completo se conserva entonces el orden de carga/recepcion y cada
        # fila seguira como «Procesando» hasta que su cantidad cuadre.
        if Counter(lote.paginas for lote in nuevos) != esperadas:
            propios = sorted(grupo, key=lambda t: (
                t.manifiesto.creado, t.manifiesto.parte,
                str(t.carpeta).casefold(),
            ))
            remotos = sorted(
                nuevos, key=lambda lote: (lote.recibido, lote.batch_id)
            )
            for trabajo, lote in zip(propios, remotos):
                subida = trabajo.manifiesto.etapas.get("subir")
                if subida and subida.estado is EstadoEtapa.EN_CURSO:
                    subida.marcar(EstadoEtapa.HECHA, "recuperado en AirVault")
                trabajo.anotar_lote(cliente, lote)
                reclamados.add(lote.batch_id.strip().upper())
            continue
        por_paginas: dict[int, List["Trabajo"]] = {}
        for trabajo in grupo:
            por_paginas.setdefault(len(trabajo.manifiesto.registros), []).append(
                trabajo
            )
        lotes_por_paginas: dict[int, List[ResumenLote]] = {}
        for lote in nuevos:
            lotes_por_paginas.setdefault(lote.paginas, []).append(lote)
        for paginas, propios in por_paginas.items():
            propios.sort(key=lambda t: (
                t.manifiesto.creado, t.manifiesto.parte,
                str(t.carpeta).casefold(),
            ))
            remotos = sorted(
                lotes_por_paginas[paginas],
                key=lambda lote: (lote.recibido, lote.batch_id),
            )
            for trabajo, lote in zip(propios, remotos):
                subida = trabajo.manifiesto.etapas.get("subir")
                if subida and subida.estado is EstadoEtapa.EN_CURSO:
                    subida.marcar(EstadoEtapa.HECHA, "recuperado en AirVault")
                trabajo.anotar_lote(cliente, lote)
                reclamados.add(lote.batch_id.strip().upper())


def comprobar_partes(
    trabajos: Sequence["Trabajo"], cliente, avisar: Optional[Aviso] = None,
) -> List[EstadoParte]:
    """Mira en que va cada parte en AirVault. No escribe nada.

    Es lo que responde «¿ya se subio?». Una sola consulta a la cola sirve
    para todas las partes, y de paso ubica las que todavia no se habian
    encontrado, asi que se puede repetir cada tanto sin cargar el
    servidor. Que un lote tarde en aparecer no es un fallo: AirVault lo
    procesa en su cola y puede tardar minutos u horas.
    """
    if avisar is not None:
        avisar("Preguntando a AirVault por los batches", 0, 0)
    lotes = list(cliente.listar_lotes())
    _asignar_batches_nuevos(trabajos, cliente, lotes)
    return [_estado_de(trabajo, cliente, lotes) for trabajo in trabajos]


def completar_partes(
    trabajos: Sequence["Trabajo"], cliente, avisar: Optional[Aviso] = None,
) -> List[Tuple["Trabajo", ResultadoCompletar]]:
    """Da por terminados los lotes que AirVault vaya a aceptar.

    El que tenga una sola pagina fuera de verde se queda en la cola con el
    motivo anotado. Un lote que no se deja cerrar no corta a los demas:
    son lotes distintos y lo escrito en cada uno ya esta escrito.
    """
    hechos: List[Tuple["Trabajo", ResultadoCompletar]] = []
    for trabajo in trabajos:
        if trabajo.manifiesto.solo_subir:
            # El lote de Revisar existe justamente para que una persona lo
            # indexe a mano; cerrarlo seria archivarlo sin mirarlo.
            continue
        cabeza = _prefijo(trabajo)
        if avisar is not None:
            avisar(f"{cabeza}Cerrando el lote en AirVault", 0, 0)
        try:
            hechos.append((trabajo, trabajo.completar(cliente)))
        except Exception as exc:  # noqa: BLE001 - se anota y siguen los demas
            logger.warning(
                "No se pudo cerrar el lote {}: {}",
                trabajo.manifiesto.batch_id, exc,
            )
            hechos.append((trabajo, ResultadoCompletar(
                False, [], 0, f"AirVault no lo acepto: {exc}"
            )))
    return hechos


def planificar_partes(
    trabajos: Sequence["Trabajo"], cliente,
    resolutor: Optional[ResolutorFlota] = None, sobrescribir: bool = False,
    avisar: Optional[Aviso] = None,
) -> List[Tuple[Plan, Indexador]]:
    """Calcula el plan de cada parte sin escribir nada en ninguna."""
    resolutor = resolutor or ResolutorFlota()
    planes: List[Tuple[Plan, Indexador]] = []
    for trabajo in trabajos:
        cabeza = _prefijo(trabajo)

        def propio(texto: str, hechas: int, total: int,
                   cabeza: str = cabeza) -> None:
            if avisar is not None:
                avisar(f"{cabeza}{texto}", hechas, total)

        try:
            planes.append(trabajo.planificar(
                cliente, resolutor, sobrescribir, propio if avisar else None
            ))
        except BaseException:
            # Una parte que falla no puede dejar tomadas las anteriores:
            # son lotes distintos y ya nadie va a escribir en ellos.
            cerrar_partes(trabajos[:len(planes)], cliente)
            raise
    return planes


def cerrar_partes(trabajos: Sequence["Trabajo"], cliente) -> None:
    """Suelta en AirVault todos los lotes que el recorrido dejo abiertos."""
    for trabajo in trabajos:
        trabajo.cerrar(cliente)


def indexar_partes(
    trabajos: Sequence["Trabajo"], planes: Sequence[Tuple[Plan, Indexador]],
    detener_en_error: bool = True, avisar: Optional[Aviso] = None,
) -> Resultado:
    """Escribe todas las partes y devuelve el resultado sumado.

    El avance se cuenta sobre el total de la corrida, no sobre cada parte:
    quien mira la barra quiere saber cuanto falta para terminar, no cuanto
    falta del archivo tres.
    """
    total = sum(len(plan.escribibles) for plan, _indexador in planes)
    hechas = 0
    sumado = Resultado()
    for trabajo, (plan, indexador) in zip(trabajos, planes):
        cabeza = _prefijo(trabajo)
        arrastre = hechas

        def propio(texto: str, propias: int, _suyas: int,
                   cabeza: str = cabeza, arrastre: int = arrastre) -> None:
            if avisar is not None:
                avisar(f"{cabeza}{texto}", arrastre + propias, total)

        resultado = trabajo.indexar(
            indexador, plan, detener_en_error, propio if avisar else None
        )
        hechas += resultado.escritas
        sumado.escritas += resultado.escritas
        sumado.omitidas += resultado.omitidas
        sumado.fallidas += resultado.fallidas
        sumado.separadores_borrados += resultado.separadores_borrados
        sumado.separadores_pendientes += resultado.separadores_pendientes
        sumado.detalles.extend(
            f"{cabeza}{detalle}" for detalle in resultado.detalles
        )
        if resultado.interrumpido:
            # Se cayo la sesion o la red: las partes que faltan no se
            # intentan siquiera, y lo escrito queda anotado para retomarlo.
            sumado.interrumpido = resultado.interrumpido
            break
        if resultado.fallidas and detener_en_error:
            break
    return sumado


def verificar_partes(
    trabajos: Sequence["Trabajo"], cliente
) -> Tuple[int, int, List[str]]:
    """Relee todas las partes y suma como quedaron."""
    validas = total = 0
    problemas: List[str] = []
    for trabajo in trabajos:
        if trabajo.manifiesto.solo_subir:
            # No se escribio nada en el: comprobar que quedo valido seria
            # reprochar que nadie lo haya indexado todavia.
            continue
        cabeza = _prefijo(trabajo)
        propias, suyas, suyos = trabajo.verificar(cliente)
        validas += propias
        total += suyas
        problemas.extend(f"{cabeza}{p}" for p in suyos)
    return validas, total, problemas

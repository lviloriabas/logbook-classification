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

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Mapping, Optional, Sequence, Tuple

from loguru import logger

from app.airvault import manifest as manifiestos
from app.airvault.config import AirVaultConfig
from app.airvault.client import ResumenLote
from app.airvault.discovery import (
    LoteNoEncontrado,
    buscar_por_id,
    normalizar_nombre,
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
from app.airvault.model import EstadoEtapa, Manifiesto
from app.airvault.naming import (
    PREFIJO_POR_DEFECTO,
    nombre_de_parte,
    nombre_de_revisar,
    nombre_desde_corrida,
)

CARPETA_TRABAJOS = Path("output") / "airvault"

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
            return nombre_de_revisar(base)
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
    # El archivo de revisar no se numera: no es «una de cinco», es el que
    # queda aparte. Numerarlo correria la cuenta de las demas.
    numerables = sum(1 for p in indice if not p.get("revisar"))
    partes: List[ParteDeEntrega] = []
    numero = 0
    for parte in indice:
        nombre = str(parte.get("pdf", "")).strip()
        es_revisar = bool(parte.get("revisar"))
        if not es_revisar:
            numero += 1
        partes.append(ParteDeEntrega(
            indice=numero, total=numerables,
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
            if mismo_csv:
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
            config, carpeta, csv, nombre_lote, prefijo, resolutor, parte
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
        if plan.escribibles:
            self.tomar(indexador.cliente)
        try:
            resultado = indexador.aplicar(plan, detener_en_error, avanzar)
        finally:
            # Tambien si se corto a medias: lo escrito queda escrito y el
            # lote no se queda bloqueado por un trabajo que ya no corre.
            self.cerrar(indexador.cliente)
        self.manifiesto.etapa("indexar").marcar(
            EstadoEtapa.HECHA if not resultado.fallidas else EstadoEtapa.ERROR,
            f"escritas {resultado.escritas}, omitidas {resultado.omitidas}, "
            f"fallidas {resultado.fallidas}",
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
        return carpeta / "revisar"
    if parte.total <= 1:
        return carpeta
    return carpeta / f"parte-{parte.indice:02d}"


def preparar_partes(
    config: AirVaultConfig, carpeta: Path | str, csv: Path | str,
    nombre_lote: str = "", prefijo: str = PREFIJO_POR_DEFECTO,
    resolutor: Optional[ResolutorFlota] = None,
) -> List["Trabajo"]:
    """Un trabajo por cada archivo de entrega de la corrida.

    Cada parte es un lote distinto en AirVault, con su nombre, su
    manifiesto y sus guardas. Repartirlas asi es lo que deja que una parte
    se caiga o se retome sin arrastrar a las demas.
    """
    partes = comprobar_entrega(csv)
    resolutor = resolutor or ResolutorFlota()
    return [
        Trabajo.abrir_o_preparar(
            config, carpeta_de_parte(carpeta, parte), csv,
            nombre_lote, prefijo, resolutor, parte,
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
    """Sube el archivo de cada parte que no se haya subido ya.

    Entre una parte y la siguiente se espera a que la anterior aparezca en
    la cola. AirVault junta en un mismo lote los archivos que le llegan
    seguidos —comprobado subiendo la entrega y la parte de Revisar una
    detras de otra: quedaron las dos en un solo lote de 33 paginas—, y dos
    partes en el mismo lote no se pueden indexar por separado, que es justo
    para lo que se reparten.

    Detras de la ultima no se espera a nada. Ahi la subida termina; que
    AirVault haya acabado de procesarla es otra cosa, puede tardar mucho
    mas, y se pregunta despues con :func:`comprobar_partes` en vez de
    dejar el programa esperando delante.
    """
    ultima = len(trabajos) - 1
    for indice, trabajo in enumerate(trabajos):
        cabeza = _prefijo(trabajo)

        def propio(texto: str, hechas: int, total: int,
                   cabeza: str = cabeza) -> None:
            if avisar is not None:
                avisar(f"{cabeza}{texto}", hechas, total)

        trabajo.subir(sesion, avisar=propio if avisar else None,
                      cliente=cliente)
        if cliente is None or trabajo.manifiesto.batch_id:
            continue
        if indice < ultima:
            trabajo.descubrir(cliente, True, dormir,
                              propio if avisar else None)
        else:
            _ubicar(trabajo, cliente, list(cliente.listar_lotes()))


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
    partes = partes_de_corrida(csv)
    if not partes:
        return []
    trabajos: List["Trabajo"] = []
    for parte in partes:
        propia = carpeta_de_parte(carpeta, parte)
        if not manifiestos.existe(propia):
            return []
        trabajo = Trabajo.cargar(config, propia)
        if Path(trabajo.manifiesto.csv_origen or "") != Path(csv).resolve():
            # La carpeta guarda el trabajo de otra ejecucion; retomarlo
            # escribiria los datos de una en el lote de otra.
            return []
        trabajos.append(trabajo)
    return trabajos


def estado_local(trabajo: "Trabajo") -> EstadoParte:
    """En que va una parte segun su manifiesto, sin preguntar a AirVault.

    Sirve para pintar la lista en cuanto se elige una ejecucion. Lo que no
    puede saber es si el servidor ya termino de procesar el lote: eso solo
    lo dice :func:`comprobar_partes`, que si pregunta.
    """
    manifiesto = trabajo.manifiesto
    if manifiesto.etapa_hecha("completar"):
        return EstadoParte(trabajo, COMPLETADO, "cerrado en AirVault")
    if not manifiesto.etapa_hecha("subir"):
        return EstadoParte(trabajo, SIN_SUBIR, "todavia sin subir")
    if manifiesto.etapa_hecha("indexar"):
        return EstadoParte(
            trabajo, INDEXADO, manifiesto.etapa("indexar").detalle
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
    if manifiesto.etapa_hecha("completar"):
        return EstadoParte(trabajo, COMPLETADO, "cerrado en AirVault")
    if not manifiesto.etapa_hecha("subir"):
        return EstadoParte(trabajo, SIN_SUBIR, "todavia sin subir")
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
    if manifiesto.etapa_hecha("indexar"):
        return EstadoParte(
            trabajo, INDEXADO, manifiesto.etapa("indexar").detalle, lote
        )
    if lote.bloqueado_por:
        # Tomado por alguien, AirVault no lo entrega: abrirlo dejaria la
        # peticion colgada hasta que venza el tiempo limite.
        return EstadoParte(
            trabajo, TOMADO, f"lo tiene abierto {lote.bloqueado_por}", lote
        )
    return EstadoParte(trabajo, LISTO, f"{esperadas} paginas", lote)


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
        avisar("Preguntando a AirVault por los lotes", 0, 0)
    lotes = list(cliente.listar_lotes())
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

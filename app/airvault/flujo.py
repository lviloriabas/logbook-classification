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
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

from loguru import logger

from app.airvault import manifest as manifiestos
from app.airvault.config import AirVaultConfig
from app.airvault.discovery import esperar as esperar_lote
from app.airvault.discovery import buscar as buscar_lote
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
    nombre_desde_corrida,
)

CARPETA_TRABAJOS = Path("output") / "airvault"

# Avisos de avance: reciben un texto y, cuando se sabe, cuanto se lleva de
# cuanto. Es lo que la interfaz convierte en barra de progreso.
Aviso = Callable[[str, int, int], None]


class ErrorDeCorrida(RuntimeError):
    """La corrida no trae lo que hace falta para indexarla."""


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

    def nombre_lote(self, base: str) -> str:
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
    total = len(indice)
    partes: List[ParteDeEntrega] = []
    for numero, parte in enumerate(indice, start=1):
        nombre = str(parte.get("pdf", "")).strip()
        partes.append(ParteDeEntrega(
            indice=numero, total=total,
            pdf=carpeta / nombre if nombre else carpeta,
            paginas=list(parte.get("paginas") or []),
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


class Trabajo:
    """Un trabajo de indexado: su manifiesto y las etapas que lo mueven."""

    def __init__(self, config: AirVaultConfig, carpeta: Path,
                 manifiesto: Manifiesto):
        self.config = config
        self.carpeta = Path(carpeta)
        self.manifiesto = manifiesto

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
              avisar: Optional[Aviso] = None) -> None:
        """Sube el PDF de la corrida por Quick Upload.

        Se salta sola si el lote ya se subio en un intento anterior: volver
        a subirlo crearia un segundo lote con el mismo nombre, y con nombres
        repetidos no hay forma de saber en cual escribir.
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
        valores = valores_de_indice(
            self.manifiesto.registros[0], self.manifiesto.doc_type,
            self.manifiesto.audit_status, self.manifiesto.nombre_batch,
        )
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
        if esperar:
            lote = esperar_lote(
                cliente.listar_lotes, nombre, self.manifiesto.repo_id,
                esperadas, self.config.espera_descubrimiento_s,
                self.config.espera_maxima_s, dormir=dormir,
            )
        else:
            lote = buscar_lote(
                cliente.listar_lotes(), nombre, self.manifiesto.repo_id,
                esperadas,
            )
        self.manifiesto.batch_id = lote.batch_id
        self.manifiesto.etapa("descubrir").marcar(
            EstadoEtapa.HECHA, f"{lote.batch_id} ({lote.paginas} paginas)"
        )
        self.guardar()
        return lote.batch_id

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
        info = cliente.abrir_lote(self.manifiesto.batch_id)
        paginas = int((info or {}).get("pageCount", 0) or 0)
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
        plan = indexador.planificar(paginas)
        self.guardar()
        return plan, indexador

    def indexar(
        self, indexador: Indexador, plan: Plan,
        detener_en_error: bool = True, avisar: Optional[Aviso] = None,
    ) -> Resultado:
        """Escribe las paginas del plan que quedaron habilitadas."""
        self.manifiesto.etapa("indexar").marcar(EstadoEtapa.EN_CURSO)
        self.guardar()
        avanzar = None
        if avisar is not None:
            def avanzar(hechas: int, previstas: int) -> None:
                avisar("Escribiendo en AirVault", hechas, previstas)
        resultado = indexador.aplicar(plan, detener_en_error, avanzar)
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


# ── la corrida entera, parte por parte ─────────────────────────────

def carpeta_de_parte(carpeta: Path | str, parte: ParteDeEntrega) -> Path:
    """Carpeta del trabajo de una parte dentro del trabajo de la corrida.

    Con una sola parte se usa la carpeta tal cual, que es donde han vivido
    siempre los trabajos de una corrida sin repartir.
    """
    carpeta = Path(carpeta)
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
    if manifiesto.partes <= 1:
        return ""
    return f"Parte {manifiesto.parte} de {manifiesto.partes}: "


def subir_partes(trabajos: Sequence["Trabajo"], sesion,
                 avisar: Optional[Aviso] = None) -> None:
    """Sube el archivo de cada parte que no se haya subido ya."""
    for trabajo in trabajos:
        cabeza = _prefijo(trabajo)

        def propio(texto: str, hechas: int, total: int,
                   cabeza: str = cabeza) -> None:
            if avisar is not None:
                avisar(f"{cabeza}{texto}", hechas, total)

        trabajo.subir(sesion, avisar=propio if avisar else None)


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

        planes.append(trabajo.planificar(
            cliente, resolutor, sobrescribir, propio if avisar else None
        ))
    return planes


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
        cabeza = _prefijo(trabajo)
        propias, suyas, suyos = trabajo.verificar(cliente)
        validas += propias
        total += suyas
        problemas.extend(f"{cabeza}{p}" for p in suyos)
    return validas, total, problemas

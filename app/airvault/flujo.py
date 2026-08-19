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
from app.airvault.naming import PREFIJO_POR_DEFECTO, nombre_desde_corrida

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


def pdf_unico_de_corrida(csv: Path | str) -> Path:
    """El unico PDF de la corrida, o un error que explica que falta.

    Se exige uno solo a proposito. El orden de las paginas del lote en
    AirVault es el del archivo que se subio, y con varios archivos no hay
    forma de saber si el servidor los junta en un lote ni en que orden los
    encadena. Equivocarse ahi no deja un hueco: escribe la matricula de un
    avion en la bitacora de otro.
    """
    encontrados = pdfs_de_corrida(csv)
    if not encontrados:
        raise ErrorDeCorrida(
            "La corrida no tiene ningun PDF de entrega. Hay que exportarla "
            "antes de subirla a AirVault."
        )
    if len(encontrados) > 1:
        nombres = ", ".join(p.name for p in encontrados[:4])
        raise ErrorDeCorrida(
            f"La corrida tiene {len(encontrados)} PDF ({nombres}...). Para "
            f"indexar hace falta uno solo: volver a exportarla con la salida "
            f"en un solo PDF."
        )
    return encontrados[0]


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
    ) -> "Trabajo":
        """Arma el manifiesto a partir del CSV y del PDF de la corrida.

        El orden manda el PDF, no el CSV: el archivo que se sube lleva
        separadores entre las secciones y el lote de AirVault tendra una
        pagina por cada uno. Si se contaran solo las bitacoras, todo lo que
        va detras del primer separador se escribiria una pagina corrida.
        """
        resolutor = resolutor or ResolutorFlota()
        filas = leer_csv_corrida(csv)
        indice = leer_indice_paginas(ruta_indice_paginas(csv))
        if indice:
            registros = registros_desde_entrega(filas, indice, resolutor)
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
            nombre_batch=nombre_lote or nombre_desde_corrida(csv, prefijo),
            repo_id=config.repo_id,
            csv_origen=str(Path(csv).resolve()),
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
                if nombre_lote:
                    trabajo.manifiesto.nombre_batch = nombre_lote
                    trabajo.guardar()
                return trabajo
            logger.info(
                "El trabajo {} era de otra corrida; se rehace", carpeta.name
            )
        return cls.preparar(
            config, carpeta, csv, nombre_lote, prefijo, resolutor
        )

    def guardar(self) -> Path:
        return manifiestos.guardar(self.manifiesto, self.carpeta)

    # ── etapas ─────────────────────────────────────────────────────

    def subir(self, sesion, pdf: Path | str,
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
        archivo = Path(pdf)
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

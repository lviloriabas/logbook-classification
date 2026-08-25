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
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import (
    Callable, Collection, Dict, List, Mapping, Optional, Sequence, Tuple,
)

from loguru import logger

from app.airvault import manifest as manifiestos
from app.airvault.client import ResumenLote
from app.airvault.config import (
    CAMPO_BATCH_NAME,
    CAMPO_END_DATE,
    CAMPO_LOG_NUMBER,
    CAMPO_MATRICULA,
    CAMPOS_OBLIGATORIOS,
    AirVaultConfig,
    nombre_campo,
)
from app.airvault.discovery import (
    LoteAmbiguo,
    LoteNoEncontrado,
    normalizar_nombre,
    recien_llegados,
)
from app.airvault.discovery import buscar as buscar_lote
from app.airvault.discovery import esperar as esperar_lote
from app.airvault.indexer import Indexador, Plan, Resultado, verificar_lote
from app.airvault.mapping import (
    ResolutorFlota,
    leer_csv_corrida,
    leer_indice_paginas,
    registros_desde_csv,
    registros_desde_entrega,
    fecha_airvault,
    normalizar_log_number,
    normalizar_matricula,
    valores_de_indice,
)
from app.airvault import registro as registro_entrega
from app.airvault.model import (
    EstadoEtapa,
    EstadoRegistro,
    Etapa,
    Manifiesto,
    Registro,
)
from app.airvault.naming import (
    PREFIJO_POR_DEFECTO,
    nombre_de_parte,
    nombre_de_revisar,
    nombre_desde_corrida,
)

CARPETA_TRABAJOS = Path("output") / "airvault"

# Revisiones completas de nombre, páginas y contenido antes de empezar el
# reloj de espera para una posible resubida. Son ciclos de comprobación, no
# reenvíos: en todos se vuelve a leer la cola de AirVault.
INTENTOS_IDENTIFICACION_ANTES_DE_ESPERA = 3

# El programa no deja de reintentar una carga que no llego: quedarse
# esperando no la arregla, y lo que hay que conseguir es que el batch acabe
# en AirVault. Lo que crece es el margen entre un intento y el siguiente,
# multiplicando la espera configurada por los reenvios ya hechos: media
# hora, una hora, hora y media. Asi una cola que solo va lenta no recibe el
# mismo archivo cada vuelta del reloj, y una carga realmente perdida acaba
# subiendo igual.

# La pantalla de Quick Upload declara 2048 MB por archivo. La compresion
# conserva margen respecto de ese techo y usa una calidad JPEG moderada:
# bajar de 300/600 DPI a 200 aporta la mayor parte del ahorro sin castigar
# los numeros y trazos manuscritos con una cuantizacion agresiva.
DPI_COMPRESION = 200
CALIDAD_JPEG_COMPRESION = 88
MAXIMO_QUICK_UPLOAD_BYTES = 2048 * 1024 * 1024

# Avisos de avance: reciben un texto y, cuando se sabe, cuanto se lleva de
# cuanto. Es lo que la interfaz convierte en barra de progreso.
Aviso = Callable[[str, int, int], None]


class ErrorDeCorrida(RuntimeError):
    """La ejecución no trae lo que hace falta para indexarla."""


class PaginasAmarillas(ErrorDeCorrida):
    """El batch subiria paginas sin algun campo obligatorio.

    No es un fallo del programa ni un dato corrupto: es una decision. Lo
    mejor es que esas bitacoras vayan al batch REVISAR, y para eso hay que
    volver a exportar la ejecución. Pero cuando el archivo ya esta hecho,
    rehacerlo cuesta mas que terminar de indexar esas paginas a mano en
    AirVault, asi que quien sube puede autorizarlo y seguir.

    Lleva la lista de paginas y lo que le falta a cada una para que quien
    pregunte pueda enseñarlo sin volver a calcularlo ni leer el texto del
    error.
    """

    def __init__(self, nombre_batch: str, paginas: Sequence[str]):
        self.nombre_batch = nombre_batch
        self.paginas = list(paginas)
        super().__init__(
            f"El batch automático «{nombre_batch}» dejaría "
            f"{len(self.paginas)} páginas amarillas ({resumen_amarillas(self.paginas)}). "
            "No se subió porque nadie lo ha autorizado todavía. Lo limpio es "
            "volver a exportar para que esas páginas queden en el batch "
            "REVISAR; si prefiere subirlo así e indexarlas a mano en "
            "AirVault, haga clic derecho sobre su fila y elija «Subir a "
            "AirVault ahora»: se le preguntará y podrá autorizarlo."
        )


def resumen_amarillas(paginas: Sequence[str], cuantas: int = 5) -> str:
    """Las primeras paginas amarillas y cuantas quedan detras."""
    muestra = "; ".join(paginas[:cuantas])
    resto = len(paginas) - cuantas
    if resto > 0:
        muestra += f"; y {resto} más"
    return muestra


def paginas_amarillas(trabajo: "Trabajo") -> List[str]:
    """Paginas que quedarian sin indexar si este batch se subiera ahora.

    Amarillo es como AirVault pinta una pagina a la que le falta un campo
    obligatorio. Se sabe sin preguntarle nada al servidor: basta con el
    manifiesto, asi que la ventana puede consultarlo antes de arrancar el
    hilo y preguntar a tiempo.

    El batch REVISAR existe justamente para recoger lo dudoso, asi que en el
    no hay nada que avisar: se sube sabiendo que se indexa a mano.
    """
    manifiesto = trabajo.manifiesto
    if manifiesto.solo_subir:
        return []
    incompletas: List[str] = []
    for registro in manifiesto.bitacoras():
        valores = valores_de_indice(
            registro,
            manifiesto.doc_type,
            manifiesto.audit_status,
            manifiesto.nombre_batch,
        )
        faltantes = [
            nombre_campo(campo)
            for campo in CAMPOS_OBLIGATORIOS
            if not str(valores.get(campo, "") or "").strip()
        ]
        if faltantes:
            incompletas.append(
                f"página {registro.seq}: {', '.join(faltantes)}"
            )
    return incompletas


def autorizar_amarillas(trabajo: "Trabajo", permitir: bool = True) -> None:
    """Deja dicho que este batch se puede subir con paginas amarillas.

    Queda en el manifiesto y no en la llamada: la subida puede fallar por
    otra cosa y reintentarse sola, y volver a preguntar lo mismo seria
    pedirle a alguien que autorice dos veces la misma decision.
    """
    trabajo.manifiesto.amarillas_permitidas = bool(permitir)
    trabajo.guardar()


def paginas_de_lote(info) -> int:
    """Cuantas paginas dice AirVault que tiene el batch recien abierto.

    Devuelve 0 cuando la respuesta no lo trae, que es una situacion real
    (batch a medio procesar, batch borrado) y la guarda de cantidad explica
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
    """Carpeta de la ejecución a partir de su CSV (``<corrida>/datos/x.CSV``)."""
    ruta = Path(csv).resolve()
    return ruta.parent.parent if ruta.parent.name == "datos" else ruta.parent


def ruta_indice_paginas(csv: Path | str) -> Path:
    """Indice de paginas que la ejecución deja junto al CSV."""
    from app.reports.organize import NOMBRE_INDICE_PAGINAS

    ruta = Path(csv)
    return ruta.with_name(f"{ruta.stem}{NOMBRE_INDICE_PAGINAS}")


def pdfs_de_corrida(csv: Path | str) -> List[Path]:
    """PDFs de entrega que dejo la ejecución, en orden de nombre."""
    carpeta = carpeta_de_corrida(csv)
    if not carpeta.is_dir():
        return []
    return sorted(p for p in carpeta.glob("*.pdf") if p.is_file())


@dataclass(frozen=True)
class ParteDeEntrega:
    """Un archivo de la entrega, que sera un batch propio en AirVault."""

    indice: int
    total: int
    pdf: Path
    paginas: List[dict]
    # El archivo con las bitacoras que requieren revision. Se sube e indexa
    # con todos los datos confirmados, pero se conserva abierto para corregir
    # lo dudoso.
    revisar: bool = False

    def nombre_lote(self, base: str) -> str:
        if self.revisar:
            return nombre_de_parte(nombre_de_revisar(base), self.indice, self.total)
        return nombre_de_parte(base, self.indice, self.total)


def partes_de_corrida(csv: Path | str) -> List[ParteDeEntrega]:
    """Archivos de entrega de la ejecución, con lo que lleva cada uno.

    Sale del indice que escribe la exportacion, no de listar la carpeta: el
    indice dice ademas en que orden van las paginas dentro de cada archivo,
    que es lo unico que permite emparejarlas con el batch sin adivinar.
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
        partes.append(
            ParteDeEntrega(
                indice=indice_parte,
                total=total_partes,
                pdf=carpeta / nombre if nombre else carpeta,
                paginas=list(parte.get("paginas") or []),
                revisar=es_revisar,
            )
        )
    return partes


def comprobar_entrega(csv: Path | str) -> List[ParteDeEntrega]:
    """Partes de la ejecución, o un error que explica que le falta."""
    partes = partes_de_corrida(csv)
    if not partes:
        if pdfs_de_corrida(csv):
            raise ErrorDeCorrida(
                "La ejecución se exporto antes de que existiera el indice de "
                "paginas. Hay que volver a exportarla para poder indexarla."
            )
        raise ErrorDeCorrida(
            "La ejecución no tiene ningun PDF de entrega. Hay que exportarla "
            "antes de subirla a AirVault."
        )
    faltan = [p.pdf.name for p in partes if not p.pdf.is_file()]
    if faltan:
        raise ErrorDeCorrida(
            f"El indice nombra archivos que no estan en la carpeta de la "
            f"ejecución: {', '.join(faltan[:4])}. Volver a exportarla."
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
    origen: ParteDeEntrega,
    numero_origen: int,
    paginas: Sequence[int],
    carpeta: Path,
    comprimir: bool = False,
    avisar: Optional[Aviso] = None,
    fuente_pdf: Optional[Path] = None,
) -> Path:
    """Prepara un tramo contiguo y estable para Quick Upload.

    Sin compresion copia las paginas como estan. Con compresion rasteriza cada
    pagina a 200 DPI y la guarda en JPEG de calidad moderada. ``fuente_pdf``
    permite cortar despues un PDF que ya fue comprimido, sin rasterizar cada
    tramo por separado. En ambos casos la entrega exportada queda intacta.
    """
    from app.vision.pdf_loader import copy_pdf_pages

    fuente = Path(fuente_pdf or origen.pdf)
    numeros = list(paginas)
    seleccion = ",".join(str(numero) for numero in numeros)
    huella = hashlib.sha1(
        (str(origen.pdf.resolve()).casefold() + "|" + seleccion).encode("utf-8")
    ).hexdigest()[:10]
    clase = "revisar" if origen.revisar else "automatico"
    destino = (
        carpeta / "cargas" / f"{clase}-{numero_origen:02d}-{huella}-"
        f"p{numeros[0] + 1:05d}-{numeros[-1] + 1:05d}-{huella}"
        f"{'-200dpi' if comprimir or fuente_pdf is not None else ''}.pdf"
    )
    esperadas = len(numeros)
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
        if comprimir:
            import pymupdf as fitz

            documento_fuente = fitz.open(str(fuente))
            salida = fitz.open()
            try:
                for desplazamiento, numero_pagina in enumerate(numeros, start=1):
                    if avisar is not None:
                        avisar(
                            f"Comprimiendo {origen.pdf.name} a {DPI_COMPRESION} DPI",
                            desplazamiento - 1,
                            esperadas,
                        )
                    pagina = documento_fuente.load_page(numero_pagina)
                    pixmap = pagina.get_pixmap(
                        dpi=DPI_COMPRESION,
                        colorspace=fitz.csRGB,
                        alpha=False,
                    )
                    imagen = pixmap.tobytes("jpeg", jpg_quality=CALIDAD_JPEG_COMPRESION)
                    nueva = salida.new_page(
                        width=pagina.rect.width, height=pagina.rect.height
                    )
                    nueva.insert_image(nueva.rect, stream=imagen)
                salida.save(str(temporal), garbage=4, deflate=True)
            finally:
                salida.close()
                documento_fuente.close()
        else:
            copy_pdf_pages(
                ((fuente, pagina + 1) for pagina in numeros),
                temporal,
            )
        os.replace(temporal, destino)
    finally:
        temporal.unlink(missing_ok=True)
    return destino


def _partir_paginas_por_seccion(
    paginas: Sequence[dict],
    limite: int,
    seleccion: Optional[Sequence[int]] = None,
) -> List[List[int]]:
    """Índices de páginas para batches del tamaño exacto que se pidió.

    Todos los batches llevan ``limite`` páginas y solo el último se queda
    con el resto. Antes se cortaba entre aeronaves, y como una sección que
    no cabía entera cerraba el batch antes de tiempo, con el mismo límite
    salían batches de tamaños distintos y muy por debajo de lo pedido.

    Cortar por cantidad puede dejar una aeronave repartida entre dos
    batches. Cuando eso pasa, el batch siguiente abre con una copia de su
    separador (que ocupa una de sus páginas) para que ninguno empiece con
    bitácoras huérfanas.

    ``seleccion`` acota qué páginas del PDF entran en el reparto, en el
    orden en que van a viajar. Sirve para rellenar los huecos que dejaron
    batches ya subidos: el separador que encabeza cada tramo se busca en el
    PDF completo, así que se repite aunque su propia página se quedara en
    otro batch.
    """
    orden = (
        list(seleccion) if seleccion is not None else list(range(len(paginas)))
    )
    if not orden:
        return []

    # Separador que encabeza la sección de cada página. Una página que es
    # separador se encabeza a sí misma, así que empezar justo en ella no
    # repite nada.
    cabeceras: List[Optional[int]] = []
    vigente: Optional[int] = None
    for indice, pagina in enumerate(paginas):
        if pagina.get("separador"):
            vigente = indice
        cabeceras.append(vigente)

    # Sin límite va todo en un tramo, que es como se pide «no repartir».
    tramo = limite if limite > 0 else len(orden) + 1
    partes: List[List[int]] = []
    cursor = 0
    while cursor < len(orden):
        cabecera = cabeceras[orden[cursor]]
        repetir = cabecera is not None and cabecera != orden[cursor]
        if repetir and tramo < 2:
            raise ErrorDeCorrida(
                "El límite de una página no permite repetir el separador "
                "junto con una bitácora; elija al menos 2 páginas por batch"
            )
        actual = [cabecera] if repetir else []
        fin = min(len(orden), cursor + tramo - len(actual))
        actual.extend(orden[cursor:fin])
        partes.append(actual)
        cursor = fin
    return partes


def _seleccion_pendiente(
    paginas: Sequence[dict],
    ya_cubiertas: Collection[Tuple[str, int]],
) -> List[int]:
    """Paginas de esta parte que ningun batch comprometido se llevo aun.

    Un separador entra cuando su seccion conserva alguna bitacora pendiente.
    Si la seccion entera ya viajo, sobra: nadie quedaria detras de el.
    """
    if not ya_cubiertas:
        return list(range(len(paginas)))
    pendiente_por_seccion: List[bool] = [False] * len(paginas)
    cabecera: Optional[int] = None
    for indice, pagina in enumerate(paginas):
        if pagina.get("separador"):
            cabecera = indice
            continue
        clave = clave_de_pagina(pagina)
        if clave is not None and clave in ya_cubiertas:
            continue
        pendiente_por_seccion[indice] = True
        if cabecera is not None:
            pendiente_por_seccion[cabecera] = True
    return [
        indice for indice, pendiente in enumerate(pendiente_por_seccion)
        if pendiente
    ]


def partes_para_airvault(
    partes: Sequence[ParteDeEntrega],
    carpeta: Path | str,
    paginas_por_batch: int | None = None,
    avisar: Optional[Aviso] = None,
    compresion: bool = False,
    ya_cubiertas: Collection[Tuple[str, int]] = (),
    numeracion_desde: Optional[Mapping[bool, int]] = None,
) -> List[ParteDeEntrega]:
    """Acota los PDF que recibira Quick Upload sin tocar la entrega original.

    Cada tramo conserva el mismo orden y los mismos diccionarios del indice
    de paginas. Solo los archivos que exceden el limite se copian a la
    carpeta interna del trabajo. Si se activa la compresion, cada archivo de
    entrega se rasteriza una vez a 200 DPI antes de dividirlo en batches; los
    tramos se copian desde ese PDF comprimido sin modificar la entrega
    original.
    Automaticos y REVISAR se numeran por separado.

    ``ya_cubiertas`` son las bitacoras que ya viajaron en batches que estan
    en AirVault: se quedan fuera del reparto para que no suban dos veces.
    ``numeracion_desde`` dice cuantas partes de cada clase existen ya, de
    modo que las nuevas sigan la cuenta en vez de repetir sus nombres.
    """
    try:
        # Sin preferencia explícita no se reparte: quien llama solo para
        # comprimir conserva la entrega original y no introduce un número
        # fijo escondido.
        limite = int(paginas_por_batch or 0)
    except (TypeError, ValueError) as exc:
        raise ErrorDeCorrida("El limite de paginas por batch no es valido") from exc
    if limite < 0:
        raise ErrorDeCorrida("El limite de paginas por batch no puede ser negativo")

    crudas: List[tuple[Path, List[dict], bool]] = []
    total_paginas = sum(len(parte.paginas) for parte in partes)
    preparadas = 0
    for numero_origen, parte in enumerate(partes, start=1):
        cantidad = len(parte.paginas)
        completa = list(range(cantidad))
        seleccion = _seleccion_pendiente(parte.paginas, ya_cubiertas)
        if not seleccion:
            # Todas sus bitacoras viajaron ya en batches que estan en
            # AirVault. Ni se comprime ni se corta: no queda nada que subir.
            continue
        necesita_comprobar = (
            compresion
            or (limite > 0 and cantidad > limite)
            or seleccion != completa
        )
        if necesita_comprobar:
            paginas_pdf = _paginas_del_pdf(parte.pdf)
            if paginas_pdf != cantidad:
                raise ErrorDeCorrida(
                    f"El PDF {parte.pdf.name} tiene {paginas_pdf} paginas y su "
                    f"indice declara {cantidad}; no se puede repartir sin "
                    "desalinear las bitacoras. Vuelva a exportar la ejecucion."
                )

        fuente_pdf = parte.pdf
        if compresion:
            fuente_pdf = _pdf_de_carga(
                parte,
                numero_origen,
                range(cantidad),
                Path(carpeta),
                comprimir=True,
                avisar=avisar,
            )

        indices_por_batch = _partir_paginas_por_seccion(
            parte.paginas, limite, seleccion
        )
        for indices in indices_por_batch:
            if avisar is not None:
                avisar(
                    f"Preparando batches de {limite} páginas",
                    preparadas,
                    total_paginas,
                )
            if indices == completa and not compresion:
                pdf = parte.pdf
            elif indices == completa and compresion:
                pdf = fuente_pdf
            else:
                pdf = _pdf_de_carga(
                    parte,
                    numero_origen,
                    indices,
                    Path(carpeta),
                    avisar=avisar,
                    fuente_pdf=fuente_pdf if compresion else None,
                )
            crudas.append(
                (
                    pdf,
                    [parte.paginas[indice] for indice in indices],
                    parte.revisar,
                )
            )
            preparadas += len(indices)

    previas = dict(numeracion_desde or {})
    resultado: List[ParteDeEntrega] = []
    for revisar in (False, True):
        propias = [p for p in crudas if p[2] is revisar]
        ya_hechas = max(0, int(previas.get(revisar, 0)))
        for indice, (pdf, paginas, _revisar) in enumerate(
            propias, start=ya_hechas + 1
        ):
            resultado.append(
                ParteDeEntrega(
                    indice=indice,
                    total=len(propias) + ya_hechas,
                    pdf=pdf,
                    paginas=paginas,
                    revisar=revisar,
                )
            )
    return resultado


@dataclass(frozen=True)
class ResultadoCompletar:
    """Como quedo el intento de dar un batch por terminado."""

    completado: bool
    # Paginas que impiden cerrarlo, por numero de pagina del batch.
    bloqueadas: List[int]
    paginas: int
    detalle: str = ""
    # Separadores del PDF que se quitaron del batch para poder cerrarlo.
    quitadas: List[int] = field(default_factory=list)


def _enumerar(numeros: Sequence[int], cuantos: int = 8) -> str:
    """Lista corta de paginas para un mensaje, sin volcarlas todas."""
    cabeza = ", ".join(str(n) for n in list(numeros)[:cuantos])
    resto = len(numeros) - cuantos
    return f"{cabeza} y {resto} mas" if resto > 0 else cabeza


# ── en que va cada parte en AirVault ───────────────────────────────
#
# Un batch recien subido no esta listo al instante: AirVault lo procesa en
# su cola y puede tardar minutos u horas. Estos son los estados por los
# que pasa, y son lo que se consulta cada tanto en vez de dejar el
# programa esperando delante.

SIN_SUBIR = "sin_subir"
BUSCANDO = "buscando"
PROCESANDO = "procesando"
DESCUADRADO = "descuadrado"
LISTO = "listo"
INCOMPLETO = "incompleto"
TOMADO = "tomado"
SOLO_REVISAR = "solo_revisar"
INDEXADO = "indexado"
COMPLETADO = "completado"
AUTOCOMPLETADO = "autocompletado"
CANCELADO = "cancelado"

NOMBRE_ESTADO_PARTE = {
    SIN_SUBIR: "Sin subir",
    BUSCANDO: "Subido pendiente confirmación",
    PROCESANDO: "Procesándose en AirVault",
    DESCUADRADO: "Cantidad de páginas incorrecta",
    LISTO: "Listo para indexar",
    INCOMPLETO: "Indexado incompleto",
    TOMADO: "Abierto por otra persona",
    SOLO_REVISAR: "Indexar lo disponible y revisar",
    INDEXADO: "Indexado",
    COMPLETADO: "Terminado",
    AUTOCOMPLETADO: "Terminado por el programa",
    CANCELADO: "Cancelado en la cola",
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
        return self.estado in (LISTO, INCOMPLETO, SOLO_REVISAR) and not (
            self.lote and self.lote.bloqueado_por
        )

    @property
    def se_puede_subir(self) -> bool:
        """Si se le puede *ordenar* a este archivo que vaya a Quick Upload.

        Ojo con confundirla con :func:`partes_por_subir`, que responde otra
        pregunta: que manda el programa por su cuenta. Esa sigue siendo la
        unica regla de la automatizacion y no se toca. Esta dice hasta donde
        llega la mano de quien mira la tabla, y por eso es mas ancha: no
        pregunta si conviene, pregunta si es seguro.

        Vale mientras AirVault no haya devuelto un batch para esta parte, y
        da igual por que motivo no lo devolvio: que la carga no llegara, que
        lleve dos revisiones de tres, que aparezca con las paginas
        descuadradas o que el reloj de espera no haya vencido. Sin batch
        confirmado no hay nada que duplicar.

        Con un batch confirmado la accion se apaga: volver a subirlo dejaria
        dos copias del mismo PDF en la cola de Web Index, y eso si cuesta
        trabajo deshacerlo.
        """
        return self.lote is None and self.estado not in (
            INDEXADO, COMPLETADO, AUTOCOMPLETADO, CANCELADO,
        )

    @property
    def se_acabo(self) -> bool:
        """Ya no hay nada que esperar de esta parte."""
        return self.estado in (
            DESCUADRADO, INDEXADO, COMPLETADO, AUTOCOMPLETADO, CANCELADO,
        )

    def __str__(self) -> str:
        titulo = NOMBRE_ESTADO_PARTE.get(self.estado, self.estado)
        if self.lote is not None:
            # Solo se puede afirmar que la subida quedo confirmada cuando
            # AirVault devolvio el batch en el indice. El ID guardado en el
            # manifiesto no basta: puede venir de una subida anterior o de
            # una carga que Quick Upload acepto pero aun no publico.
            titulo = f"Subido confirmado; {titulo}"
        return f"{titulo}: {self.detalle}" if self.detalle else titulo


class Trabajo:
    """Un trabajo de indexado: su manifiesto y las etapas que lo mueven."""

    def __init__(self, config: AirVaultConfig, carpeta: Path, manifiesto: Manifiesto):
        self.config = config
        self.carpeta = Path(carpeta)
        self.manifiesto = manifiesto
        # Si el batch esta tomado ahora mismo por este trabajo. Soltarlo dos
        # veces es un error del servidor, no una limpieza de mas.
        self._tomado = False

    # ── ciclo de vida ──────────────────────────────────────────────

    @classmethod
    def preparar(
        cls,
        config: AirVaultConfig,
        carpeta: Path | str,
        csv: Path | str,
        nombre_lote: str = "",
        prefijo: str = PREFIJO_POR_DEFECTO,
        resolutor: Optional[ResolutorFlota] = None,
        parte: Optional[ParteDeEntrega] = None,
        paginas_por_batch: int | None = None,
        compresion: bool = False,
    ) -> "Trabajo":
        """Arma el manifiesto de un archivo de entrega.

        El orden manda el PDF, no el CSV: el archivo que se sube lleva
        separadores entre las secciones y el batch de AirVault tendra una
        pagina por cada uno. Si se contaran solo las bitacoras, todo lo que
        va detras del primer separador se escribiria una pagina corrida.

        Sin ``parte`` se toma la de la ejecución, y se exige que sea una sola:
        con varias hay varios batches y el reparto lo hace
        :func:`preparar_partes`.
        """
        resolutor = resolutor or ResolutorFlota()
        limite_paginas = (
            config.paginas_por_batch
            if paginas_por_batch is None
            else paginas_por_batch
        )
        filas = leer_csv_corrida(csv)
        base = nombre_lote or nombre_desde_corrida(csv, prefijo)
        if parte is None:
            disponibles = partes_de_corrida(csv)
            if len(disponibles) > 1:
                raise ErrorDeCorrida(
                    f"La ejecución esta repartida en {len(disponibles)} partes; "
                    f"cada una es un batch distinto."
                )
            parte = disponibles[0] if disponibles else None

        if parte is not None:
            registros = registros_desde_entrega(filas, parte.paginas, resolutor)
            detalle_orden = f"{sum(1 for r in registros if r.es_separador)} separadores"
        else:
            # Ejecuciones exportadas antes de que existiera el indice. Se sigue
            # el orden del CSV, que solo coincide con el batch si el PDF no
            # llevaba ningun separador; si llevaba, la guarda de cantidad lo
            # para antes de escribir nada.
            logger.warning(
                "La ejecución no tiene indice de paginas; se asume que el PDF "
                "no lleva separadores"
            )
            registros = registros_desde_csv(filas, resolutor)
            detalle_orden = "sin indice de paginas"
        if not registros:
            raise ErrorDeCorrida(
                "El CSV de la ejecución no tiene ninguna bitacora utilizable"
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
            paginas_por_batch=int(limite_paginas or 0),
            compresion=bool(compresion),
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
    def cargar(cls, config: AirVaultConfig, carpeta: Path | str) -> "Trabajo":
        return cls(config, Path(carpeta), manifiestos.cargar(carpeta))

    @classmethod
    def abrir_o_preparar(
        cls,
        config: AirVaultConfig,
        carpeta: Path | str,
        csv: Path | str,
        nombre_lote: str = "",
        prefijo: str = PREFIJO_POR_DEFECTO,
        resolutor: Optional[ResolutorFlota] = None,
        parte: Optional[ParteDeEntrega] = None,
        paginas_por_batch: int | None = None,
        compresion: bool = False,
    ) -> "Trabajo":
        """Retoma el trabajo si ya existe para este CSV; si no, lo crea.

        Es lo que hace que apretar el boton dos veces no empiece de cero:
        un trabajo a medias conserva que paginas ya se escribieron. Si la
        carpeta guarda un trabajo de otra ejecución, se rehace: seguir con el
        anterior escribiria los datos de una ejecución en el batch de otra.
        """
        carpeta = Path(carpeta)
        limite_paginas = (
            config.paginas_por_batch
            if paginas_por_batch is None
            else paginas_por_batch
        )
        limite_paginas = int(limite_paginas or 0)
        if manifiestos.existe(carpeta):
            trabajo = cls.cargar(config, carpeta)
            mismo_csv = Path(trabajo.manifiesto.csv_origen or "") == Path(csv).resolve()
            mismo_pdf = (
                parte is None or Path(trabajo.manifiesto.pdf_origen) == parte.pdf
            )
            mismo_limite = trabajo.manifiesto.paginas_por_batch in (
                0,
                limite_paginas,
            )
            misma_compresion = trabajo.manifiesto.compresion == bool(compresion)
            if mismo_csv and mismo_pdf and mismo_limite and misma_compresion:
                propuesto = (
                    parte.nombre_lote(nombre_lote)
                    if parte and nombre_lote
                    else nombre_lote
                )
                if propuesto:
                    trabajo.manifiesto.nombre_batch = propuesto
                    trabajo.guardar()
                return trabajo
            logger.info("El trabajo {} era de otra ejecución; se rehace", carpeta.name)
        return cls.preparar(
            config,
            carpeta,
            csv,
            nombre_lote,
            prefijo,
            resolutor,
            parte,
            limite_paginas,
            compresion,
        )

    def guardar(self) -> Path:
        return manifiestos.guardar(self.manifiesto, self.carpeta)

    # ── etapas ─────────────────────────────────────────────────────

    def subir(
        self, sesion, pdf: Path | str = "", avisar: Optional[Aviso] = None, cliente=None
    ) -> None:
        """Sube el PDF de la ejecución por Quick Upload.

        Se salta sola si el batch ya se subio en un intento anterior: volver
        a subirlo crearia un segundo batch y no habria forma de saber en
        cual escribir.

        El titulo viaja en Quick Upload y normalmente nombra el batch. El
        coordinador termina de subir todos los archivos y despues confirma sus
        IDs; si AirVault publica alguno como ``Empty-Batch``, lo identifica por
        paginas y contenido antes de corregir el nombre.
        """
        from app.airvault.uploader import SubidorQuickUpload

        if self.manifiesto.etapa_hecha("subir"):
            logger.info("El batch ya estaba subido; no se vuelve a subir")
            return
        archivo = Path(pdf or self.manifiesto.pdf_origen)
        if not archivo.is_file():
            raise ErrorDeCorrida(f"No esta el archivo de entrega {archivo.name}")
        paginas_pdf = _paginas_del_pdf(archivo)
        paginas_indice = len(self.manifiesto.registros)
        limite = int(self.manifiesto.paginas_por_batch or 0)
        if paginas_pdf != paginas_indice:
            raise ErrorDeCorrida(
                f"El PDF {archivo.name} tiene {paginas_pdf} paginas y el "
                f"indice declara {paginas_indice}; no se sube porque los "
                "datos quedarian corridos. Vuelva a exportar la ejecucion."
            )
        if limite > 0 and paginas_pdf > limite:
            raise ErrorDeCorrida(
                f"El PDF {archivo.name} tiene {paginas_pdf} paginas y supera "
                f"el maximo elegido de {limite}; no se sube. Reinicie el "
                "registro local para volver a repartir la ejecucion."
            )
        incompletas = paginas_amarillas(self)
        if incompletas and not self.manifiesto.amarillas_permitidas:
            raise PaginasAmarillas(self.manifiesto.nombre_batch, incompletas)
        if incompletas:
            logger.warning(
                "Se sube «{}» con {} paginas amarillas: lo autorizaron "
                "expresamente",
                self.manifiesto.nombre_batch,
                len(incompletas),
            )
        if archivo.stat().st_size > MAXIMO_QUICK_UPLOAD_BYTES:
            raise ErrorDeCorrida(
                f"El PDF {archivo.name} pesa mas de 2048 MB, que es el "
                "maximo de Quick Upload; no se sube. Reduzca el maximo de "
                "paginas por batch y reinicie el registro local."
            )
        if cliente is not None:
            # AirVault publica lo cargado como ``Empty-Batch`` aunque Quick
            # Upload reciba C_BatchName. La unica identidad segura es la
            # diferencia contra la cola tomada inmediatamente antes.
            self.manifiesto.lotes_previos = [
                lote.batch_id for lote in cliente.listar_lotes()
            ]
            self.guardar()
        # De la primera bitacora, no de la primera pagina: la primera
        # suele ser un separador, sin avion, y Aircraft es obligatorio en
        # Quick Upload. Estos valores son solo la clasificacion inicial del
        # archivo; lo de cada pagina lo escribe el indexado despues.
        bitacoras = self.manifiesto.bitacoras()
        primera = bitacoras[0] if bitacoras else self.manifiesto.registros[0]
        valores = valores_de_indice(
            primera,
            self.manifiesto.doc_type,
            self.manifiesto.audit_status,
            self.manifiesto.nombre_batch,
        )
        subidor = SubidorQuickUpload(sesion, self.manifiesto.repo_id)
        self.manifiesto.etapa("subir").marcar(EstadoEtapa.EN_CURSO)
        self.guardar()
        resultado = subidor.subir(archivo, valores, avisar=avisar)
        if not resultado.ok:
            self.manifiesto.etapa("subir").marcar(EstadoEtapa.ERROR, resultado.detalle)
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
        self,
        cliente,
        esperar: bool = True,
        dormir: Callable[[float], None] = time.sleep,
        avisar: Optional[Aviso] = None,
        cache: Optional[dict[str, str]] = None,
    ) -> str:
        """Ubica el batch en AirVault por su nombre y lo deja anotado.

        Un batch recien subido tarda en cruzar el procesamiento del servidor,
        asi que no aparecer todavía no es un error hasta que vence el limite
        de espera.
        """
        esperadas = len(self.manifiesto.registros)
        nombre = self.manifiesto.nombre_batch
        if avisar is not None:
            avisar(f"Buscando el batch {nombre} en AirVault", 0, 0)
        error_busqueda: Optional[Exception] = None
        lote = None
        try:
            if esperar:
                lote = esperar_lote(
                    cliente.listar_lotes,
                    nombre,
                    self.manifiesto.repo_id,
                    esperadas,
                    self.config.espera_descubrimiento_s,
                    self.config.espera_maxima_s,
                    dormir=dormir,
                    previos=self.manifiesto.lotes_previos or None,
                )
            else:
                lote = buscar_lote(
                    cliente.listar_lotes(),
                    nombre,
                    self.manifiesto.repo_id,
                    esperadas,
                )
        except (LoteAmbiguo, LoteNoEncontrado) as exc:
            error_busqueda = exc
            lote = None

        # La búsqueda por título solo espera a que AirVault publique algo.
        # Incluso un título completo se confirma después con páginas y
        # contenido; de otro modo un nombre puesto al ID equivocado pasa al
        # indexado sin que nadie lo advierta.
        lotes_actuales = cliente.listar_lotes()
        nombres_embebidos = cache if cache is not None else {}
        lote = _lote_por_identidad_y_contenido(
            self,
            cliente,
            lotes_actuales,
            avisar=avisar,
            cache=nombres_embebidos,
        )
        if lote is None:
            if (
                isinstance(error_busqueda, LoteNoEncontrado)
                and not any(
                    _es_nombre_provisional(actual.nombre, nombre)
                    or _nombre_visible_compatible(actual.nombre, nombre)
                    for actual in lotes_actuales
                )
            ):
                raise error_busqueda
            raise ErrorDeCorrida(
                f"AirVault todavía no permite confirmar cuál batch "
                f"corresponde a «{nombre}». No se renombró ni vinculó "
                "ninguno: el programa volverá a contrastar la cantidad de "
                "páginas, el Batch Name y los Log Page Number internos, y "
                "solo aceptará una coincidencia única."
            )
        return self.anotar_lote(cliente, lote, avisar)

    def anotar_lote(
        self, cliente, lote: ResumenLote, avisar: Optional[Aviso] = None
    ) -> str:
        """Da por propio el batch encontrado y le pone su nombre.

        Esta aparte de :meth:`descubrir` porque la comprobacion periodica
        ubica batches sin esperar a nada, y encontrarlo tiene que dejar el
        trabajo igual de anotado se haya llegado por donde se haya llegado.
        """
        self._ponerle_nombre(cliente, lote, avisar)
        # El ID solo queda ligado al trabajo después de que AirVault confirma
        # el título. Guardarlo antes permitía continuar e indexar aunque el
        # batch siguiera indistinguible como ``Empty-Batch``.
        self.manifiesto.batch_id = lote.batch_id
        self.manifiesto.intentos_identificacion = 0
        self.manifiesto.busquedas_amplias_sin_hallar = 0
        self.manifiesto.espera_reenvio_desde = ""
        # AirVault lo publico: la carga no se perdio y el tope de reenvios
        # vuelve a cero para el proximo trabajo que use este manifiesto.
        self.manifiesto.reenvios = 0
        self.manifiesto.etapa("descubrir").marcar(
            EstadoEtapa.HECHA, f"{lote.batch_id} ({lote.paginas} paginas)"
        )
        self.guardar()
        registro_entrega.anotar(self.carpeta, [self])
        return lote.batch_id

    def _ponerle_nombre(self, cliente, lote, avisar: Optional[Aviso] = None) -> None:
        """Deja el batch con su nombre en la cola de AirVault.

        Quick Upload recibe el nombre normalmente. Esta correccion solo se
        usa cuando AirVault lo pierde y publica la carga como «Empty-Batch».
        """
        nombre = self.manifiesto.nombre_batch
        if not nombre or normalizar_nombre(lote.nombre) == normalizar_nombre(nombre):
            return
        compatibles = self.manifiesto.cantidades_paginas_compatibles()
        if lote.paginas not in compatibles:
            esperadas = " o ".join(str(n) for n in sorted(compatibles))
            raise ErrorDeCorrida(
                f"No se renombró el Empty-Batch {lote.batch_id}: tiene "
                f"{lote.paginas} páginas y el archivo «{nombre}» debe tener "
                f"{esperadas}."
            )
        renombrar = getattr(cliente, "renombrar_lote", None)
        if renombrar is None:
            raise ErrorDeCorrida(
                f"AirVault recibió el batch {lote.batch_id}, pero esta conexión "
                "no permite ponerle el título esperado. No se indexó para no "
                "dejar otro Empty-Batch."
            )
        if avisar is not None:
            avisar(
                f"Batch {lote.batch_id}: corrigiendo nombre a «{nombre}»",
                0,
                0,
            )

        # UpdateBatchName puede contestar por HTTP antes de que GetBatches
        # refleje el cambio. Se envia una vez y se consulta pronto, con pausas
        # crecientes: la propagacion normal se confirma en menos de un segundo
        # sin repetir escrituras, pero se conserva margen para AirVault lento.
        pausas = (0.0, 0.25, 0.5, 1.0, 2.0, 4.0)
        aceptado = renombrar(lote.batch_id, nombre)
        for intento, pausa in enumerate(pausas):
            if pausa and aceptado:
                time.sleep(pausa)
            try:
                actuales = cliente.listar_lotes(nombre)
            except TypeError:
                actuales = cliente.listar_lotes()
            confirmado = next(
                (
                    actual
                    for actual in actuales
                    if str(actual.batch_id).strip().upper()
                    == str(lote.batch_id).strip().upper()
                    and normalizar_nombre(actual.nombre)
                    == normalizar_nombre(nombre)
                ),
                None,
            )
            if confirmado is not None:
                if avisar is not None:
                    avisar(
                        f"Batch {lote.batch_id}: nombre corregido",
                        0,
                        0,
                    )
                return
            if intento < len(pausas) - 1 and not aceptado:
                if avisar is not None and not aceptado:
                    avisar(
                        f"Batch {lote.batch_id}: nombre no aceptado; "
                        "reintentando",
                        0,
                        0,
                    )
                aceptado = renombrar(lote.batch_id, nombre)
        raise ErrorDeCorrida(self._mensaje_nombre_no_confirmado(lote))

    def _mensaje_nombre_no_confirmado(self, lote: ResumenLote) -> str:
        nombre = self.manifiesto.nombre_batch
        return (
            f"AirVault recibió el archivo como ID {lote.batch_id}, pero no "
            f"confirmó el título «{nombre}» y todavía puede figurar como "
            f"«{lote.nombre or 'Empty-Batch'}». No se indexó ninguna página. "
            "El programa seguirá comprobándolo, leerá el Batch Name interno "
            "y volverá a identificarlo y renombrarlo automáticamente."
        )

    def fijar_lote(self, batch_id: str) -> None:
        """Salta la busqueda y apunta el batch a mano."""
        self.manifiesto.batch_id = str(batch_id).strip()
        self.manifiesto.etapa("descubrir").marcar(
            EstadoEtapa.HECHA, f"fijado a mano: {self.manifiesto.batch_id}"
        )
        self.guardar()

    def planificar(
        self,
        cliente,
        resolutor: Optional[ResolutorFlota] = None,
        sobrescribir: bool = False,
        avisar: Optional[Aviso] = None,
    ) -> Tuple[Plan, Indexador]:
        """Calcula el plan completo sin escribir nada.

        Es el mismo camino que sigue la escritura, asi que lo que muestra el
        reporte es lo que se va a enviar y no una version resumida.
        """
        if not self.manifiesto.batch_id:
            raise ErrorDeCorrida(
                "El trabajo todavía no tiene batch. Hay que buscarlo primero."
            )
        info = self._abrir_lote(cliente)
        paginas = paginas_de_lote(info)
        try:
            picklist = cliente.picklist_matriculas()
        except Exception as exc:  # noqa: BLE001 - el catalogo no es critico
            logger.warning("No se pudo leer el picklist de matriculas: {}", exc)
            picklist = []
        if avisar is not None:
            avisar("Leyendo las paginas del batch", 0, paginas)

        def persistir(_manifiesto: Manifiesto) -> None:
            self.guardar()

        indexador = Indexador(
            cliente,
            self.manifiesto,
            picklist,
            sobrescribir,
            al_guardar=persistir,
            resolutor=resolutor or ResolutorFlota(),
        )
        try:
            plan = indexador.planificar(paginas)
        finally:
            # Planificar solo lee. Antes el batch se quedaba tomado hasta
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
        """Toma el batch para escribirlo, y dice quien lo tiene si no se deja.

        Escribir una pagina exige ser el dueno del batch. Se toma justo
        antes de escribir y se suelta al terminar, en vez de quedarselo
        desde la revision: un batch tomado no da error, deja colgada la
        siguiente apertura.
        """
        return self._abrir_lote(cliente)

    def _abrir_lote(self, cliente):
        """Toma el batch y, si no contesta, dice quien lo tiene tomado.

        AirVault admite un solo dueno por batch y no responde «esta
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
                f"El batch {self.manifiesto.nombre_batch} esta abierto por "
                f"{dueno} y AirVault no lo entrega a nadie mas: la peticion "
                f"se queda esperando sin contestar. Hay que cerrarlo en "
                f"AirVault (abrirlo y salir con Close) y volver a intentar."
            ) from exc
        self._tomado = True
        return info

    def _quien_lo_tiene(self, cliente) -> str:
        """Usuario que tiene tomado el batch, o vacio si no se pudo saber."""
        try:
            lotes = cliente.listar_lotes(self.manifiesto.nombre_batch)
        except Exception as exc:  # noqa: BLE001 - es solo para el mensaje
            logger.debug("No se pudo averiguar quien tiene el batch: {}", exc)
            return ""
        for lote in lotes or []:
            if lote.batch_id == self.manifiesto.batch_id:
                return lote.bloqueado_por
        return ""

    def cerrar(self, cliente) -> None:
        """Suelta el batch en AirVault. No levanta: es limpieza.

        Se llama al terminar y tambien cuando algo se corta a medias. Un
        batch que queda tomado no da error: cuelga la siguiente apertura,
        que es mucho peor de diagnosticar.

        Soltar dos veces no se intenta. El batch de Revisar ya se suelta al
        planificarlo; volver a pedirlo antes de indexar hacia
        que AirVault contestara «Batch no esta tomado por este usuario» con
        un 500, que ademas se reintentaba tres veces y terminaba en un
        aviso que hacia pensar que el batch habia quedado colgado.
        """
        batch_id = self.manifiesto.batch_id
        if not batch_id or not self._tomado:
            return
        self._tomado = False
        try:
            cliente.cerrar_lote(batch_id)
        except Exception as exc:  # noqa: BLE001 - cerrar nunca tumba nada
            logger.warning(
                "No se pudo desbloquear el batch {} en AirVault: {}. Si la "
                "siguiente apertura se queda esperando, hay que quitarle "
                "el bloqueo alli a mano.",
                batch_id,
                exc,
            )
        else:
            logger.info("Batch {} desbloqueado en AirVault", batch_id)

    def indexar(
        self,
        indexador: Indexador,
        plan: Plan,
        detener_en_error: bool = True,
        avisar: Optional[Aviso] = None,
    ) -> Resultado:
        """Escribe las paginas del plan que quedaron habilitadas.

        Toma el batch antes de escribir y lo suelta al terminar. La revision
        ya no se lo queda: entre revisar y escribir puede pasar un rato
        largo, y AirVault admite un solo dueno.
        """
        self.manifiesto.etapa("indexar").marcar(EstadoEtapa.EN_CURSO)
        self.guardar()
        avanzar = None
        if avisar is not None:

            def avanzar(hechas: int, previstas: int) -> None:
                avisar("Escribiendo en AirVault", hechas, previstas)

        if plan.escribibles or (plan.separadores and not self.manifiesto.solo_subir):
            self.tomar(indexador.cliente)
        try:
            resultado = indexador.aplicar(plan, detener_en_error, avanzar)
        finally:
            # Tambien si se corto a medias: lo escrito queda escrito y el
            # batch no se queda bloqueado por un trabajo que ya no corre.
            self.cerrar(indexador.cliente)
        con_error = bool(
            resultado.fallidas
            or resultado.separadores_pendientes
            or resultado.interrumpido
        )
        detalle = (
            f"escritas {resultado.escritas}, omitidas {resultado.omitidas}, "
            f"fallidas {resultado.fallidas}, separadores borrados "
            f"{resultado.separadores_borrados}"
        )
        if resultado.separadores_pendientes:
            detalle += f", separadores sin borrar {resultado.separadores_pendientes}"
        self.manifiesto.etapa("indexar").marcar(
            EstadoEtapa.ERROR if con_error else EstadoEtapa.HECHA, detalle
        )
        self.guardar()
        return resultado

    def verificar(self, cliente) -> Tuple[int, int, Sequence[str]]:
        """Relee el batch y confirma contra el servidor como quedo."""
        validas, total, problemas = verificar_lote(cliente, self.manifiesto)
        self.manifiesto.etapa("verificar").marcar(
            EstadoEtapa.HECHA if validas == total else EstadoEtapa.ERROR,
            f"{validas}/{total} en Valid",
        )
        self.guardar()
        return validas, total, problemas

    def completar(
        self, cliente, automatico: bool = False
    ) -> "ResultadoCompletar":
        """Da el batch por terminado: lo indexa y lo manda a Web Search.

        AirVault solo lo acepta con **todas** las paginas en verde: basta
        una a la que le falte un campo obligatorio (casi siempre la fecha)
        para que no deje cerrar el batch. Asi que primero se miran las
        paginas y, si alguna bloquea, no se intenta: se dice cuales son y
        el batch se queda en la cola, que es justo donde tiene que quedarse
        para que alguien las arregle.

        La misma comprobacion que hace la pantalla antes de habilitar su
        boton «Complete»: cuenta la pagina que encabeza cada documento,
        salvo las borradas.

        Las paginas separadoras del PDF (la matricula de cada grupo,
        «REVISAR», «POSIBLES DISCREPANCIAS») tambien cuentan, y nunca van a
        estar en verde: no son bitacoras, no tienen fecha ni avion que
        escribirles. Como no son documentos, se quitan del batch antes de
        cerrarlo, que es lo mismo que hace a mano quien indexa. Se quitan
        solo si con eso el batch queda cerrable: si ademas hay una bitacora
        en amarillo el batch no se cierra hoy, y entonces mas vale no
        haberlo tocado.
        """
        if self.manifiesto.solo_subir:
            return ResultadoCompletar(
                False,
                [],
                len(self.manifiesto.registros),
                "el batch REVISAR se conserva abierto para corregir los "
                "datos que no pudieron confirmarse",
            )
        batch_id = self.manifiesto.batch_id or ""
        if not batch_id:
            raise ErrorDeCorrida(
                "El trabajo todavía no tiene batch; no hay nada que terminar."
            )
        paginas = list(cliente.paginas_del_lote(batch_id))
        separadores = {r.seq for r in self.manifiesto.separadores()}
        bloqueadas = [
            p.pagina
            for p in paginas
            if (
                not p.borrada
                and p.encabeza_documento
                and not p.valida
                and p.pagina not in separadores
            )
        ]
        # Todo separador debe salir del batch automatico, incluso si por un
        # estado remoto raro ya aparece verde. Publicarlo crearia un
        # documento sin bitacora; limitarse a los amarillos no lo evita.
        quitables = [
            p.pagina for p in paginas if not p.borrada and p.pagina in separadores
        ]
        if bloqueadas:
            detalle = (
                f"{len(bloqueadas)} de {len(paginas)} paginas no estan en "
                f"verde ({_enumerar(bloqueadas)}); AirVault no deja cerrar "
                f"el batch hasta que se completen"
            )
            self.manifiesto.etapa("completar").marcar(EstadoEtapa.OMITIDA, detalle)
            self.guardar()
            logger.info("El batch {} no se cierra: {}", batch_id, detalle)
            return ResultadoCompletar(False, bloqueadas, len(paginas), detalle)
        self.tomar(cliente)
        quitadas: List[int] = []
        try:
            for numero in quitables:
                if cliente.borrar_pagina(batch_id, numero, True):
                    quitadas.append(numero)
            # La respuesta de MarkPageDeleted no basta: se relee el mapa,
            # igual que la tira de paginas del cliente web, y solo se cree
            # que se borro lo que el servidor ya marca como borrado.
            tras_borrar = list(cliente.paginas_del_lote(batch_id))
            presentes = {p.pagina for p in tras_borrar if not p.borrada}
            faltan = [n for n in quitables if n in presentes]
            quitadas = [n for n in quitables if n not in presentes]
            if faltan:
                # Quitar paginas pide un permiso aparte. Sin el, el batch se
                # queda como estaba y hay que sacarlas en AirVault a mano.
                detalle = (
                    f"quedaron {len(faltan)} paginas separadoras en el batch "
                    f"({_enumerar(faltan)}) y AirVault no deja cerrarlo con "
                    f"ellas; hace falta el permiso «Delete Batch Image»"
                )
                self.cerrar(cliente)
                self.manifiesto.etapa("completar").marcar(EstadoEtapa.OMITIDA, detalle)
                self.guardar()
                logger.info("El batch {} no se cierra: {}", batch_id, detalle)
                return ResultadoCompletar(
                    False, faltan, len(paginas), detalle, quitadas
                )

            # El boton Complete de AirVault ejecuta esta validacion de todo
            # el batch antes de FormsProcessing/CompleteBatch. MXDocs la
            # tiene activa y puede devolver a amarillo una pagina que parecia
            # verde por tener llenos los campos obligatorios.
            cabeceras = [
                p.pagina
                for p in tras_borrar
                if not p.borrada and p.encabeza_documento and p.valida
            ]
            cliente.validar_batch(batch_id, cabeceras)
            tras_validar = list(cliente.paginas_del_lote(batch_id))
            rechazadas = [
                p.pagina
                for p in tras_validar
                if not p.borrada and p.encabeza_documento and not p.valida
            ]
            if rechazadas:
                detalle = (
                    f"la validacion final de AirVault dejo "
                    f"{len(rechazadas)} paginas fuera de verde "
                    f"({_enumerar(rechazadas)}); no se envio CompleteBatch"
                )
                self.cerrar(cliente)
                self.manifiesto.etapa("completar").marcar(EstadoEtapa.OMITIDA, detalle)
                self.manifiesto.etapa("verificar").marcar(
                    EstadoEtapa.ERROR,
                    f"{len(tras_validar) - len(rechazadas)}/"
                    f"{len(tras_validar)} en Valid despues de validar",
                )
                self.guardar()
                logger.info(
                    "El batch {} no se completa despues de validarlo: {}",
                    batch_id,
                    detalle,
                )
                return ResultadoCompletar(
                    False, rechazadas, len(paginas), detalle, quitadas
                )
            cliente.completar_lote(batch_id)
        except BaseException:
            self.cerrar(cliente)
            raise
        # Terminado, el batch ya no esta tomado: soltarlo seria pedirle a
        # AirVault que suelte algo que ya solto.
        self._tomado = False
        detalle = f"{len(paginas) - len(quitadas)} paginas en verde"
        if quitadas:
            detalle += f", {len(quitadas)} separadores quitados del batch"
        self.manifiesto.completado_automatico = bool(automatico)
        self.manifiesto.etapa("completar").marcar(EstadoEtapa.HECHA, detalle)
        self.guardar()
        registro_entrega.anotar(self.carpeta, [self])
        logger.info("Batch {} dado por terminado en AirVault", batch_id)
        return ResultadoCompletar(True, [], len(paginas), detalle, quitadas)


# ── la ejecución entera, parte por parte ─────────────────────────────


# ── que bitacoras cubre ya cada batch ──────────────────────────────
#
# Cambiar el reparto de una entrega que ya tiene batches en AirVault es la
# unica forma de duplicar o perder una bitacora sin que nadie se entere: los
# batches viejos siguen ahi con sus paginas y el reparto nuevo no sabe de
# ellos. Todo lo que decide que se conserva y que se vuelve a repartir sale
# de aqui, de la identidad estable de cada bitacora en la entrega:
# el archivo del que salio y su pagina dentro de el.


def clave_de_pagina(entrada: Mapping[str, object]) -> Optional[Tuple[str, int]]:
    """Identidad de una pagina del indice, o ``None`` si es un separador."""
    if str(entrada.get("separador", "") or "").strip():
        return None
    archivo = str(entrada.get("archivo", "") or "").strip()
    try:
        pagina = int(str(entrada.get("pagina", 0)))
    except (TypeError, ValueError):
        return None
    if not archivo or pagina <= 0:
        return None
    return (archivo.casefold(), pagina)


def paginas_cubiertas(manifiesto: Manifiesto) -> set[Tuple[str, int]]:
    """Bitacoras de la entrega que este manifiesto se lleva a su batch."""
    return {
        (registro.archivo_origen.casefold(), registro.pagina_origen)
        for registro in manifiesto.registros
        if not registro.es_separador
        and registro.archivo_origen
        and registro.pagina_origen > 0
    }


def trabajo_comprometido(trabajo: "Trabajo") -> bool:
    """Si este batch ya llego a AirVault y no se puede volver a repartir.

    ``EN_CURSO`` cuenta como comprometido a proposito: Quick Upload pudo
    aceptar el archivo y perderse la respuesta, asi que sus paginas se dan
    por enviadas hasta que alguien lo reinicie expresamente.
    """
    manifiesto = trabajo.manifiesto
    if manifiesto.batch_id:
        return True
    subida = manifiesto.etapas.get("subir")
    return bool(
        subida
        and subida.estado
        in (EstadoEtapa.HECHA, EstadoEtapa.OMITIDA, EstadoEtapa.EN_CURSO)
    )


@dataclass(frozen=True)
class Cobertura:
    """Como queda la entrega repartida entre los batches que ya existen."""

    # Bitacoras que ya viajaron en algun batch comprometido.
    cubiertas: set = field(default_factory=set)
    # Bitacoras de la entrega que no estan en ningun batch comprometido.
    # Son los huecos que hay que repartir para no perderlas.
    huecos: List[Tuple[str, int]] = field(default_factory=list)
    # Bitacoras que aparecen en mas de un batch comprometido, con los
    # nombres de esos batches. Se indexarian dos veces en AirVault.
    repetidas: Dict[Tuple[str, int], List[str]] = field(default_factory=dict)

    @property
    def completa(self) -> bool:
        return not self.huecos and not self.repetidas


def revisar_cobertura(
    partes: Sequence[ParteDeEntrega],
    trabajos: Sequence["Trabajo"],
    solo_comprometidos: bool = True,
    tambien_cubiertas: Collection[Tuple[str, int]] = (),
) -> Cobertura:
    """Contrasta la entrega contra los batches que ya existen.

    Es la comprobacion que permite cambiar el reparto sin miedo: dice que
    bitacoras ya estan en AirVault, cuales se quedarian fuera y cuales
    subirian dos veces. Por defecto solo cuenta lo comprometido, que es lo
    que ya no se puede deshacer; con ``solo_comprometidos`` en falso mide
    tambien el reparto que aun esta solo en disco.
    """
    de_la_entrega = [
        clave
        for parte in partes
        for clave in (clave_de_pagina(pagina) for pagina in parte.paginas)
        if clave is not None
    ]
    duenos: Dict[Tuple[str, int], List[str]] = {}
    for trabajo in trabajos:
        if solo_comprometidos and not trabajo_comprometido(trabajo):
            continue
        nombre = trabajo.manifiesto.nombre_batch or str(trabajo.carpeta)
        for clave in paginas_cubiertas(trabajo.manifiesto):
            duenos.setdefault(clave, []).append(nombre)
    # El registro de la entrega recuerda bitacoras que ya viajaron aunque su
    # manifiesto ya no este en disco. No cuentan como repetidas: no hay dos
    # batches llevandolas, hay uno del que solo queda la anotacion.
    cubiertas = set(duenos) | set(tambien_cubiertas)
    return Cobertura(
        cubiertas=cubiertas,
        huecos=[clave for clave in de_la_entrega if clave not in cubiertas],
        repetidas={
            clave: nombres
            for clave, nombres in duenos.items()
            if len(nombres) > 1
        },
    )


def carpeta_de_parte(carpeta: Path | str, parte: ParteDeEntrega) -> Path:
    """Carpeta del trabajo de una parte dentro del trabajo de la ejecución.

    Con una sola parte se usa la carpeta tal cual, que es donde han vivido
    siempre los trabajos de una ejecución sin repartir.
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
    config: AirVaultConfig,
    carpeta: Path | str,
    csv: Path | str,
    nombre_lote: str = "",
    prefijo: str = PREFIJO_POR_DEFECTO,
    resolutor: Optional[ResolutorFlota] = None,
    paginas_por_batch: int | None = None,
    avisar: Optional[Aviso] = None,
    compresion: bool = False,
) -> List["Trabajo"]:
    """Un trabajo por cada archivo de entrega de la ejecución.

    Cada parte es un batch distinto en AirVault, con su nombre, su
    manifiesto y sus guardas. Repartirlas asi es lo que deja que una parte
    se caiga o se retome sin arrastrar a las demas.

    Si la ejecución ya tenia batches y el reparto cambio (otro maximo de
    paginas, otra compresion, u otra forma de cortar), lo que ya esta en
    AirVault se conserva intacto y solo se reparten las bitacoras que
    ningun batch se llevo. Rehacer el reparto entero subiria dos veces lo
    ya subido; ignorar el cambio dejaria fuera lo que falta.
    """
    carpeta = Path(carpeta)
    limite_paginas = (
        config.paginas_por_batch
        if paginas_por_batch is None
        else paginas_por_batch
    )
    limite_paginas = int(limite_paginas or 0)
    entrega = comprobar_entrega(csv)
    previos = trabajos_preparados(config, carpeta, csv)
    if previos and _reparto_al_dia(previos, entrega, limite_paginas, compresion):
        # El reparto de disco sigue valiendo. Se devuelve por el camino que
        # comprueba que el juego este entero, que es el que sabe presentarlo.
        existentes = cargar_partes(config, carpeta, csv)
        if existentes:
            return existentes

    resolutor = resolutor or ResolutorFlota()
    # El registro de la entrega es la memoria que sobrevive al reparto: sabe
    # que bitacoras se mandaron aunque el manifiesto que las llevaba se haya
    # apartado o perdido. Sin el, rehacer el reparto dos veces seguidas
    # volveria a mandar lo que ya estaba en AirVault.
    cobertura = revisar_cobertura(
        entrega, previos, tambien_cubiertas=registro_entrega.comprometidas(carpeta)
    )
    if cobertura.repetidas:
        detalle = "; ".join(
            f"{archivo} p.{pagina} en {' y '.join(nombres)}"
            for (archivo, pagina), nombres in list(
                cobertura.repetidas.items()
            )[:3]
        )
        raise ErrorDeCorrida(
            f"{len(cobertura.repetidas)} bitacoras estan en mas de un batch "
            f"ya subido ({detalle}). No se reparte nada: hay que resolver esa "
            "duplicidad en AirVault antes de seguir."
        )

    comprometidos = [t for t in previos if trabajo_comprometido(t)]
    if comprometidos and avisar is not None:
        avisar(
            f"{len(comprometidos)} batches ya subidos se conservan; se "
            f"reparten las {len(cobertura.huecos)} bitácoras que faltan",
            0,
            0,
        )
    # Lo que solo existe en disco se aparta antes de repartir: su carpeta
    # puede volver a usarse, y un manifiesto huerfano se ofreceria despues
    # como un batch pendiente de subir que ya no corresponde a nada.
    registro_entrega.archivar(
        carpeta,
        f"reparto rehecho a {limite_paginas or 'sin limite'} paginas por batch",
    )
    for trabajo in previos:
        if trabajo not in comprometidos:
            _apartar_manifiesto(trabajo)

    partes = partes_para_airvault(
        entrega,
        carpeta,
        limite_paginas,
        avisar,
        compresion,
        ya_cubiertas=cobertura.cubiertas,
        numeracion_desde=_numeracion_ocupada(comprometidos),
    )
    nuevos = [
        Trabajo.abrir_o_preparar(
            config,
            carpeta_de_parte(carpeta, parte),
            csv,
            nombre_lote,
            prefijo,
            resolutor,
            parte,
            limite_paginas,
            compresion,
        )
        for parte in partes
    ]
    listos = _renumerar(_en_orden_de_parte(comprometidos + nuevos))
    registro_entrega.anotar(carpeta, listos, csv_origen=str(csv))
    return listos


def _reparto_al_dia(
    trabajos: Sequence["Trabajo"],
    entrega: Sequence[ParteDeEntrega],
    limite: int,
    compresion: bool,
) -> bool:
    """Si los manifiestos de disco ya son el reparto que se esta pidiendo.

    No basta que coincida el maximo de paginas: la forma de cortar tambien
    cambia entre versiones, y lo que decide es si entre todos cubren la
    entrega entera exactamente una vez.

    A un batch ya subido no se le exige el limite nuevo. Su reparto es
    historia y no se puede deshacer, asi que seguir contandolo como
    desfasado obligaria a reorganizar en cada llamada sin nada que cambiar.
    """
    for trabajo in trabajos:
        manifiesto = trabajo.manifiesto
        if trabajo_comprometido(trabajo):
            continue
        if manifiesto.paginas_por_batch not in (0, limite):
            return False
        if bool(manifiesto.compresion) != bool(compresion):
            return False
    return revisar_cobertura(entrega, trabajos, solo_comprometidos=False).completa


def _numeracion_ocupada(
    comprometidos: Sequence["Trabajo"],
) -> Dict[bool, int]:
    """Hasta que numero de parte llegan los batches que ya estan en AirVault.

    Las partes nuevas siguen la cuenta desde ahi. Se toma el mayor y no la
    cantidad: un numero que ya viajo con su nombre no se puede reutilizar
    aunque su hueco haya quedado libre.
    """
    ocupada: Dict[bool, int] = {False: 0, True: 0}
    for trabajo in comprometidos:
        clase = bool(trabajo.manifiesto.solo_subir)
        ocupada[clase] = max(
            ocupada[clase], int(trabajo.manifiesto.parte or 1)
        )
    return ocupada


def _apartar_manifiesto(trabajo: "Trabajo") -> None:
    """Saca de en medio el manifiesto de un reparto que se descarta.

    Se renombra en vez de borrarse: nunca llego a AirVault, asi que no hace
    falta para nada, pero si algo saliera mal el reparto anterior sigue
    ahi para mirarlo.
    """
    ruta = manifiestos.ruta_manifiesto(trabajo.carpeta)
    if not ruta.is_file():
        return
    marca = datetime.now().strftime("%Y%m%d-%H%M%S")
    destino = ruta.with_name(f"manifiesto-reemplazado-{marca}.json")
    try:
        os.replace(ruta, destino)
    except OSError:
        logger.warning("No se pudo apartar el manifiesto {}", ruta)


def _en_orden_de_parte(trabajos: Sequence["Trabajo"]) -> List["Trabajo"]:
    """Automaticos por numero de parte y despues los de REVISAR."""
    return sorted(
        trabajos,
        key=lambda t: (
            bool(t.manifiesto.solo_subir),
            int(t.manifiesto.parte or 1),
        ),
    )


def _renumerar(trabajos: Sequence["Trabajo"]) -> List["Trabajo"]:
    """Deja a cada manifiesto diciendo cuantas partes tiene su clase.

    El batch que ya estaba subido sigue creyendo que la entrega tenia las
    partes del reparto anterior. Ese numero no viaja en el nombre, pero si
    decide si el juego de manifiestos se considera completo: sin ponerlo al
    dia, la ventana dejaria de ver los batches de la ejecucion entera.
    """
    for clase in (False, True):
        suyos = [t for t in trabajos if bool(t.manifiesto.solo_subir) is clase]
        if not suyos:
            continue
        total = max(int(t.manifiesto.parte or 1) for t in suyos)
        for trabajo in suyos:
            if trabajo.manifiesto.partes != total:
                trabajo.manifiesto.partes = total
                trabajo.guardar()
    return list(trabajos)


def _prefijo(trabajo: "Trabajo") -> str:
    """Etiqueta corta e inequívoca del batch para los avisos de avance."""
    manifiesto = trabajo.manifiesto
    batch_id = str(manifiesto.batch_id or "").strip()
    if batch_id:
        return f"Batch {batch_id}: "
    if manifiesto.solo_subir:
        return "Batch REVISAR: "
    if manifiesto.partes > 1:
        return f"Batch {manifiesto.parte}/{manifiesto.partes}: "
    nombre = str(manifiesto.nombre_batch or "").strip()
    return f"Batch «{nombre}»: " if nombre else "Batch: "


def _entrega_de(trabajo: "Trabajo") -> str:
    """A que entrega pertenece un batch, normalizado para comparar.

    Hace falta porque la identidad de una bitacora (el archivo del que
    salio y su pagina) solo es unica dentro de su entrega. El escaner
    nombra igual sus archivos en cada ejecucion, asi que ``Image_001.pdf``
    pagina 1 existe en todas: sin la entrega delante, dos ejecuciones
    distintas parecen repetirse entera la una a la otra.
    """
    csv = str(trabajo.manifiesto.csv_origen or "").strip()
    if not csv:
        return ""
    try:
        return str(Path(csv).resolve()).casefold()
    except OSError:
        return csv.casefold()


def _validar_sin_bitacoras_repetidas(
    por_subir: Sequence["Trabajo"],
    contexto: Sequence["Trabajo"] = (),
) -> None:
    """Impide subir una bitacora que ya viaja en otro batch.

    Es la ultima red antes de Quick Upload y la unica que cubre lo
    irreversible: una bitacora en dos batches de AirVault se indexa dos
    veces y sale dos veces publicada. Pasa cuando el reparto se rehace
    sobre una entrega que ya tenia batches subidos, asi que se comprueba
    contra todas las partes de la ejecucion y no solo contra las que se
    van a mandar ahora.

    Cada entrega se mira por separado. La ventana puede tener delante
    batches de varias ejecuciones (los pendientes de dias anteriores se
    retoman junto a los de hoy) y entre ellas los nombres de archivo se
    repiten sin que eso signifique nada.
    """
    entregas: Dict[str, List["Trabajo"]] = {}
    for trabajo in por_subir:
        entregas.setdefault(_entrega_de(trabajo), []).append(trabajo)
    for entrega, trabajos in entregas.items():
        _validar_entrega_sin_repetidas(
            trabajos,
            [otro for otro in contexto if _entrega_de(otro) == entrega],
        )


def _validar_entrega_sin_repetidas(
    por_subir: Sequence["Trabajo"],
    contexto: Sequence["Trabajo"] = (),
) -> None:
    """La comprobacion anterior dentro de una sola entrega."""
    duenos: Dict[Tuple[str, int], str] = {}
    claves = {str(trabajo.carpeta) for trabajo in por_subir}
    # Primero lo que ya esta en AirVault: es lo que manda si algo choca.
    for trabajo in contexto:
        if str(trabajo.carpeta) in claves or not trabajo_comprometido(trabajo):
            continue
        nombre = trabajo.manifiesto.nombre_batch or str(trabajo.carpeta)
        for pagina in paginas_cubiertas(trabajo.manifiesto):
            duenos.setdefault(pagina, nombre)
    for trabajo in por_subir:
        nombre = trabajo.manifiesto.nombre_batch or str(trabajo.carpeta)
        repetidas = sorted(
            pagina for pagina in paginas_cubiertas(trabajo.manifiesto)
            if pagina in duenos and duenos[pagina] != nombre
        )
        if repetidas:
            detalle = "; ".join(
                f"{archivo} p.{pagina} ya va en «{duenos[(archivo, pagina)]}»"
                for archivo, pagina in repetidas[:3]
            )
            raise ErrorDeCorrida(
                f"El batch «{nombre}» repite {len(repetidas)} bitacoras que "
                f"ya viajan en otro batch ({detalle}); no se sube porque se "
                "indexarian dos veces en AirVault. Reinicie el registro "
                "local de la ejecucion para volver a repartirla."
            )
        for pagina in paginas_cubiertas(trabajo.manifiesto):
            duenos.setdefault(pagina, nombre)


def _validar_nombres_de_batches(trabajos: Sequence["Trabajo"]) -> None:
    """Impide subir un PDF con el titulo de otra clase de batch.

    ``REVISAR`` lleva una identidad propia y nunca puede ser el nombre de
    una division automatica. Tambien se rechazan dos manifiestos con el
    mismo titulo: AirVault no permitiria distinguir cual ID corresponde a
    cada archivo despues de subirlos.
    """
    vistos: dict[str, "Trabajo"] = {}
    for trabajo in trabajos:
        manifiesto = trabajo.manifiesto
        nombre = str(manifiesto.nombre_batch or "").strip()
        normalizado = normalizar_nombre(nombre)
        palabras = normalizado.split()
        nombre_de_revisar = bool(
            palabras
            and (
                palabras[-1] == "revisar"
                or (
                    len(palabras) >= 2
                    and palabras[-2] == "revisar"
                    and palabras[-1].isdigit()
                )
            )
        )
        if not normalizado:
            raise ErrorDeCorrida("Hay un batch sin titulo; no se sube")
        if nombre_de_revisar != manifiesto.solo_subir:
            clase = "REVISAR" if manifiesto.solo_subir else "automatico"
            raise ErrorDeCorrida(
                f"El batch {nombre!r} esta marcado como {clase}, pero su "
                "titulo corresponde a otra clase; no se sube"
            )
        if normalizado in vistos:
            raise ErrorDeCorrida(
                f"Dos archivos intentan usar el mismo titulo {nombre!r}; "
                "no se suben porque recibirian IDs ambiguos"
            )
        vistos[normalizado] = trabajo


def subir_partes(
    trabajos: Sequence["Trabajo"],
    sesion,
    avisar: Optional[Aviso] = None,
    cliente=None,
    dormir: Callable[[float], None] = time.sleep,
    al_finalizar_subidas: Optional[Callable[[Sequence["Trabajo"]], None]] = None,
    al_encontrar: Optional[Callable[["Trabajo", Sequence["Trabajo"]], None]] = None,
    reintentar_estancados: bool = False,
    en_la_ejecucion: Sequence["Trabajo"] = (),
    forzados: Collection[str] = (),
) -> List[Tuple["Trabajo", str]]:
    """Confirma todos los batches y sube solamente los que falten.

    Con cliente se buscan tambien el batch sin sufijo y los de REVISAR. Los
    encontrados conservan o recuperan su ID. Si Quick Upload ya confirmo un
    archivo pero el titulo aun no aparece en Web Index, se conserva como
    ``BUSCANDO`` y no se vuelve a cargar: AirVault puede tardar en procesarlo.
    Solo un trabajo local realmente pendiente vuelve a Quick Upload. Sin
    cliente se conserva el recorrido local para los usos antiguos del modulo.

    Justo antes de cada carga se busca otra vez su nombre. Si apareció desde
    la primera pasada, se recupera el ID y no se crea un duplicado; si sigue
    ausente, se sube y se continúa con la fila siguiente. Los callbacks capaces
    de iniciar el indexado se conservan detrás de todas las cargas. La
    instantanea de la cola tomada antes de cada subida permite reconocer el
    nombre temporal ``Empty-Batch``. Un fallo queda aislado en su trabajo: no
    impide intentar las demas partes de la ejecucion.

    ``en_la_ejecucion`` son todas las partes de la entrega cuando ``trabajos``
    es solo el subconjunto que falta por subir. Sirve para comprobar que
    ninguna bitacora que se va a mandar viaja ya en un batch subido.

    ``forzados`` son las carpetas que alguien mando subir a mano. Esas se
    saltan la comprobacion completa (que es la que tarda, por abrir cada
    batch y contrastar su contenido) y hacen en su lugar una sola lectura
    de la cola (:func:`ya_esta_en_airvault`). La persona ya miro AirVault y
    dijo que el batch no esta; lo unico que el programa sabe y ella no es
    si su carga esta publicada sin nombre, que es lo que esa lectura
    responde. Con cualquier otra respuesta se sube. Las demas partes siguen
    el camino automatico entero.
    """
    _validar_nombres_de_batches(trabajos)
    ejecucion = list(en_la_ejecucion or trabajos)
    _validar_sin_bitacoras_repetidas(trabajos, ejecucion)
    claves_forzadas = {str(clave) for clave in forzados or ()}
    por_subir = list(trabajos)
    claves_estancadas: set[str] = set()
    if cliente is not None:
        # Lo que se mando subir a mano ni se consulta: se sube. Preguntar
        # otra vez por un batch que la persona acaba de buscar en AirVault
        # es lo que hacia que una orden tardara minutos en convertirse en
        # una carga, y a veces en ninguna.
        a_confirmar = [
            trabajo for trabajo in trabajos
            if str(trabajo.carpeta) not in claves_forzadas
        ]
        estados = (
            detectar_indexados(
                comprobar_partes(a_confirmar, cliente, avisar=avisar),
                cliente,
                avisar=avisar,
            )
            if a_confirmar
            else []
        )
        estancados = [
            parte.trabajo
            for parte in estados
            if reintentar_estancados and reenvio_pendiente(parte, ejecucion)
        ]
        claves_estancadas = {str(trabajo.carpeta) for trabajo in estancados}
        claves_por_subir = claves_forzadas | claves_estancadas | {
            str(parte.trabajo.carpeta)
            for parte in estados
            if parte.estado == SIN_SUBIR
        }
        # Se filtra la lista recibida en vez de rehacerla con los estados:
        # asi las cargas forzadas conservan el orden de la tabla.
        por_subir = [
            trabajo for trabajo in trabajos
            if str(trabajo.carpeta) in claves_por_subir
        ]
        procesandose = sum(
            parte.estado in (BUSCANDO, PROCESANDO)
            and parte.trabajo not in estancados
            for parte in estados
        )
        encontrados = len(a_confirmar) - len(estancados) - procesandose - sum(
            parte.estado == SIN_SUBIR for parte in estados
        )
        if avisar is not None and a_confirmar:
            unidad = "batch" if len(a_confirmar) == 1 else "batches"
            espera = (
                f"; {procesandose} "
                f"{'sigue' if procesandose == 1 else 'siguen'} procesándose"
                if procesandose
                else ""
            )
            faltantes = sum(
                1 for trabajo in por_subir
                if str(trabajo.carpeta) not in claves_forzadas
            )
            avisar(
                f"AirVault confirmó {encontrados} de {len(a_confirmar)} "
                f"{unidad}{espera}; se subirán {faltantes} faltantes",
                0,
                0,
            )
        cuantos = sum(
            1 for trabajo in por_subir
            if str(trabajo.carpeta) in claves_forzadas
        )
        if avisar is not None and cuantos:
            unidad = "batch" if cuantos == 1 else "batches"
            avisar(
                f"Se sube {cuantos} {unidad} por orden expresa; solo se "
                "mira la cola una vez, sin abrir ningún batch",
                0,
                0,
            )
        encontrados_antes = [
            parte.trabajo for parte in estados if parte.batch_id
        ]
    else:
        encontrados_antes = []

    fallos: List[Tuple["Trabajo", str]] = []
    subidos: List["Trabajo"] = []
    for trabajo in por_subir:
        cabeza = _prefijo(trabajo)
        clave = str(trabajo.carpeta)
        forzado = clave in claves_forzadas
        permite_reenvio = clave in claves_estancadas

        if forzado and cliente is not None:
            # Una sola lectura de la cola. Es lo unico que separa obedecer
            # de duplicar: si AirVault ya publico esta carga (con su titulo
            # o, lo que es mas frecuente, como Empty-Batch sin nombre),
            # mandarla otra vez dejaria la misma bitacora publicada dos
            # veces, y eso se deshace a mano. Ojo con el orden: se mira
            # antes de reiniciar el manifiesto, porque la foto de la cola
            # anterior a la carga es justo lo que identifica un
            # Empty-Batch, y reiniciar la borra.
            try:
                publicado = ya_esta_en_airvault(trabajo, cliente)
            except LoteAmbiguo as exc:
                fallos.append((trabajo, str(exc)))
                if avisar is not None:
                    avisar(
                        f"{cabeza}Hay varios batches que podrían ser esta "
                        "carga; no se sube otra vez",
                        0,
                        0,
                    )
                continue
            if publicado is not None:
                trabajo.anotar_lote(cliente, publicado)
                if trabajo not in encontrados_antes:
                    encontrados_antes.append(trabajo)
                if avisar is not None:
                    avisar(
                        f"{cabeza}AirVault ya lo tiene publicado "
                        f"({publicado.batch_id}); no se sube otra vez",
                        0,
                        0,
                    )
                continue

        # La consulta previa a cada archivo se mantiene en el camino
        # automatico: es la que descubre un batch que AirVault publico entre
        # una consulta y la siguiente, y sin ella la carga se duplicaria.
        # La orden expresa hace arriba su version corta.
        if cliente is not None and not forzado:
            if avisar is not None:
                avisar(
                    f"{cabeza}Confirmando el nombre antes de subir",
                    0,
                    0,
                )
            estado_actual = comprobar_partes(
                [trabajo], cliente, avisar=avisar
            )[0]
            estado_actual = detectar_indexados(
                [estado_actual], cliente, avisar=avisar
            )[0]
            permite_reenvio = permite_reenvio and reenvio_pendiente(
                estado_actual, ejecucion
            )
            if estado_actual.estado != SIN_SUBIR and not permite_reenvio:
                if estado_actual.batch_id and trabajo not in encontrados_antes:
                    encontrados_antes.append(trabajo)
                if avisar is not None:
                    avisar(
                        f"{cabeza}Encontrado en AirVault; no se vuelve a subir",
                        0,
                        0,
                    )
                continue
        if permite_reenvio:
            # Se da por perdida una carga que Quick Upload si acepto. Se
            # cuenta antes de reenviarla para que el tope valga aunque el
            # envio se corte a mitad.
            trabajo.manifiesto.reenvios = (
                int(trabajo.manifiesto.reenvios or 0) + 1
            )
            trabajo.guardar()
        if cliente is not None or forzado:
            # Tambien en las forzadas: el manifiesto puede seguir creyendo
            # que el archivo ya se subio, y sin borrar esa creencia la
            # subida se saltaria sola.
            _reiniciar_subida_ausente(trabajo)

        def propio(texto: str, hechas: int, total: int, cabeza: str = cabeza) -> None:
            if avisar is not None:
                avisar(f"{cabeza}{texto}", hechas, total)

        try:
            trabajo.subir(
                sesion, avisar=propio if avisar else None, cliente=cliente
            )
        except Exception as exc:  # noqa: BLE001 - cada batch es independiente
            detalle = str(exc)
            fallos.append((trabajo, detalle))
            logger.error(
                "No se pudo subir el batch {}: {}. Se intenta el siguiente.",
                trabajo.manifiesto.nombre_batch,
                detalle,
            )
            if avisar is not None:
                avisar(
                    f"{cabeza}No se pudo subir; se intenta el siguiente batch",
                    0,
                    0,
                )
            continue
        # Quick Upload lo acepto: desde aqui esas bitacoras estan
        # comprometidas y no se vuelven a repartir aunque cambie la
        # configuracion o se pierda el manifiesto.
        registro_entrega.anotar(trabajo.carpeta, [trabajo])
        if al_finalizar_subidas is not None:
            # La UI debe reflejar que Quick Upload ya acepto este archivo
            # incluso si AirVault tarda o falla antes de publicar su ID.
            al_finalizar_subidas(trabajos)
        subidos.append(trabajo)

    # Esta es la barrera entre Quick Upload y cualquier indexado: los
    # callbacks se difieren hasta haber intentado todos los archivos.
    if al_encontrar is not None:
        for trabajo in encontrados_antes:
            al_encontrar(trabajo, trabajos)

    if cliente is not None:
        # La tabla local conserva el orden de trabajo también después de
        # Quick Upload. Cada parte empieza por buscar su título; solo el
        # propio descubrimiento cae a Empty-Batch o a otro nombre si ese
        # título no aparece. Antes se pedía primero la cola completa y se
        # recorrían las partes al revés, de modo que la bitácora empezaba
        # inspeccionando IDs remotos en vez del primer pendiente visible.
        nombres_embebidos: dict[str, str] = {}
        for trabajo in subidos:
            manifiesto = trabajo.manifiesto
            if manifiesto.batch_id:
                if al_encontrar is not None:
                    al_encontrar(trabajo, trabajos)
                continue
            cabeza = _prefijo(trabajo)

            def propio(
                texto: str,
                hechas: int,
                total: int,
                cabeza: str = cabeza,
            ) -> None:
                if avisar is not None:
                    avisar(f"{cabeza}{texto}", hechas, total)

            if avisar is not None:
                avisar(
                    f"{cabeza}Subido; buscando el batch en AirVault",
                    0,
                    0,
                )
            try:
                trabajo.descubrir(
                    cliente,
                    esperar=True,
                    dormir=dormir,
                    avisar=propio if avisar else None,
                    cache=nombres_embebidos,
                )
            except Exception as exc:  # noqa: BLE001 - se sigue con las demas
                detalle = str(exc)
                fallos.append((trabajo, detalle))
                logger.error(
                    "No se pudo encontrar el batch {}: {}. Se intenta el siguiente.",
                    trabajo.manifiesto.nombre_batch,
                    detalle,
                )
                if avisar is not None:
                    avisar(
                        f"{cabeza}AirVault aun no lo publica; se busca "
                        "el siguiente batch",
                        0,
                        0,
                    )
                continue
            if al_encontrar is not None:
                al_encontrar(trabajo, trabajos)
    return fallos


def ya_esta_en_airvault(trabajo: "Trabajo", cliente):
    """El batch de este trabajo, si AirVault ya lo tiene publicado.

    Es lo unico que precede a una subida ordenada a mano, y cuesta **una
    lectura de la cola**: todo lo demas se decide en memoria, sin abrir
    ningun batch, sin leer paginas y sin comparar Log Page Number. Lo caro
    de la comprobacion completa nunca fue el listado, sino ese contraste
    por batch; por eso esta version corta responde en el acto y aquella
    tarda minutos.

    Mira dos cosas sobre la misma lista:

    1. Un lote cuyo **nombre visible** corresponda al esperado.
    2. Un **``Empty-Batch``** que no estuviera en la cola antes de nuestra
       carga y que tenga una cantidad de paginas compatible.

    La segunda no es una rareza: AirVault publica lo cargado como
    ``Empty-Batch`` aunque Quick Upload reciba ``C_BatchName``, y por eso
    existe la foto de la cola en ``lotes_previos``. Sin esta comprobacion,
    quien mira Web Index tampoco ve su batch (no tiene nombre) y la orden
    manual crearia la segunda copia justo en el caso mas frecuente.

    Devuelve ``None`` cuando no hay nada que pueda ser esta carga, que es
    cuando la orden se cumple. Levanta :class:`LoteAmbiguo` si hay varios
    candidatos sin nombre: elegir mal seria peor que preguntar.
    """
    if cliente is None:
        return None
    manifiesto = trabajo.manifiesto
    lotes = list(_listar_cola(cliente))
    try:
        return buscar_lote(
            lotes,
            manifiesto.nombre_batch,
            manifiesto.repo_id,
            len(manifiesto.registros),
        )
    except LoteNoEncontrado:
        pass

    if not _subida_rastreable(trabajo):
        # Nunca se subio: no hay ninguna carga nuestra que AirVault pueda
        # estar mostrando sin nombre. Cualquier Empty-Batch de la cola es
        # de otro trabajo.
        return None
    candidatos = _empty_batches_que_podrian_ser(trabajo, lotes)
    if len(candidatos) > 1:
        raise LoteAmbiguo(
            f"AirVault tiene {len(candidatos)} batches sin nombre que "
            f"podrían ser «{manifiesto.nombre_batch}»: "
            + "; ".join(
                f"{candidato.batch_id} con {candidato.paginas} páginas"
                for candidato in candidatos
            )
            + ". No se subió otra vez para "
            "no dejar la misma bitácora publicada por duplicado. Mírelos en "
            "Web Index: si ninguno es esta carga, bórrelos o renómbrelos y "
            "vuelva a dar la orden."
        )
    return candidatos[0] if candidatos else None


def _empty_batches_que_podrian_ser(
    trabajo: "Trabajo", lotes: Sequence[ResumenLote]
) -> List[ResumenLote]:
    """Lotes sin nombre que aparecieron despues de subir y encajan en paginas.

    Los tres filtros son los que se pueden aplicar sin pedirle nada mas al
    servidor: que el nombre sea provisional, que no estuviera en la foto de
    la cola tomada antes de la carga, y que la cantidad de paginas sea una
    de las que puede tener esta parte.

    Sobre ese ultimo: ``cantidades_paginas_compatibles`` devuelve un solo
    numero salvo que el indexado ya hubiera confirmado separadores
    borrados, y entonces dos. Un batch asi siempre tiene lote confirmado,
    de modo que :attr:`EstadoParte.se_puede_subir` apaga la accion y por
    aqui no se llega. Si alguna vez se llegara, un batch ajeno tendria dos
    cifras con las que colar en vez de una.
    """
    manifiesto = trabajo.manifiesto
    previos = {
        str(batch_id).strip().upper()
        for batch_id in manifiesto.lotes_previos or ()
    }
    compatibles = manifiesto.cantidades_paginas_compatibles()
    return [
        lote
        for lote in lotes
        if _es_nombre_temporal(lote.nombre)
        and lote.batch_id.strip().upper() not in previos
        and lote.paginas in compatibles
        and (not lote.repo_id or lote.repo_id == manifiesto.repo_id)
    ]


def _listar_cola(cliente) -> Sequence[ResumenLote]:
    """La cola entera, en una sola peticion.

    Sin filtro a proposito: un ``Empty-Batch`` no responde a ningun nombre,
    asi que filtrar del lado del servidor lo dejaria fuera justo cuando es
    lo que hay que encontrar.
    """
    try:
        return list(cliente.listar_lotes(""))
    except TypeError:
        return list(cliente.listar_lotes())


def reiniciar_subida(trabajo: "Trabajo") -> None:
    """Olvida lo que el programa creia haber subido de este batch.

    Deja el trabajo como recien preparado (sin ID, sin etapas de subida en
    adelante y con sus registros pendientes) para que vuelva a Quick
    Upload. No toca AirVault: si el batch estaba publicado de verdad, la
    proxima busqueda lo encuentra y recupera su ID en vez de duplicarlo.
    """
    _reiniciar_subida_ausente(trabajo)


def _reiniciar_subida_ausente(trabajo: "Trabajo") -> None:
    """Quita la suposicion local de un batch que AirVault no devolvio."""
    manifiesto = trabajo.manifiesto
    manifiesto.batch_id = None
    manifiesto.lotes_previos = []
    manifiesto.intentos_identificacion = 0
    manifiesto.busquedas_amplias_sin_hallar = 0
    manifiesto.espera_reenvio_desde = ""
    for nombre in (
        "subir",
        "descubrir",
        "indexar",
        "verificar",
        "completar",
    ):
        manifiesto.etapas[nombre] = Etapa()
    for registro in manifiesto.registros:
        registro.estado = EstadoRegistro.PENDIENTE
        registro.avisos = []
    trabajo.guardar()


def _momento(marca: object) -> Optional[datetime]:
    """Lee una marca de tiempo del manifiesto, o ``None`` si no sirve."""
    texto = str(marca or "").strip()
    if not texto:
        return None
    try:
        return datetime.fromisoformat(texto)
    except (TypeError, ValueError):
        return None


def _mismo_reloj(uno: datetime, otro: datetime) -> bool:
    """Si dos marcas se pueden restar: las dos con zona o las dos sin ella."""
    return (uno.tzinfo is None) == (otro.tzinfo is None)


def momento_de_subida(trabajo: "Trabajo") -> Optional[datetime]:
    """Cuando Quick Upload acepto este archivo, segun el manifiesto.

    Es la fecha del batch, y es la que hay que comparar con la de ahora para
    saber si una carga lleva demasiado sin publicarse. El manifiesto guarda
    ademas cuando el programa empezo a esperarla, pero esa otra marca dice
    cuando alguien miro, no cuando se subio.
    """
    subida = trabajo.manifiesto.etapas.get("subir")
    if subida is None or subida.estado not in (
        EstadoEtapa.HECHA,
        EstadoEtapa.OMITIDA,
        EstadoEtapa.EN_CURSO,
    ):
        return None
    return _momento(subida.actualizada)


def subida_estancada(
    trabajo: "Trabajo",
    limite_s: float,
    ahora: Optional[datetime] = None,
) -> bool:
    """Si una subida lleva demasiado sin aparecer en Web Index.

    Quick Upload puede aceptar el archivo y aun asi no publicarlo. La espera
    no arranca a la primera: antes deben agotarse varios ciclos que revisan
    nombres, páginas y contenido, y solo entonces se mira el reloj.

    El tiempo se cuenta desde que se subio el archivo, no desde que el
    programa se dio cuenta de que no aparecia. Son cosas distintas: una
    ejecucion que se retoma dias despues estrena la marca de espera en ese
    momento, y con ella se quedaba otra media hora esperando por una carga
    que llevaba dias perdida.
    """
    inicio = _momento(trabajo.manifiesto.espera_reenvio_desde)
    if inicio is None:
        return False
    subida = momento_de_subida(trabajo)
    if subida is not None and _mismo_reloj(subida, inicio) and subida < inicio:
        inicio = subida
    presente = ahora or (
        datetime.now(inicio.tzinfo) if inicio.tzinfo else datetime.now()
    )
    if not _mismo_reloj(presente, inicio):
        return False
    try:
        return (presente - inicio).total_seconds() >= max(0.0, float(limite_s))
    except (TypeError, ValueError, OverflowError):
        return False


def subida_rebasada(
    trabajo: "Trabajo", ejecucion: Sequence["Trabajo"] = ()
) -> bool:
    """Si AirVault ya publico e indexo cargas posteriores a esta.

    Quick Upload publica lo que recibe en el orden en que lo recibe. Si las
    partes que se enviaron **despues** de esta ya aparecieron en Web Index y
    encima quedaron indexadas, la cola paso de largo: esta carga no se
    perdio en el camino de subida, se perdio en AirVault, y esperar mas no
    la va a hacer aparecer.

    Es la unica senal que no depende de un reloj. Con ella se resuelve el
    caso que dejaba una parte «buscando» para siempre: la espera vencia,
    se agotaban los reenvios y ahi se quedaba, con las siguientes ya
    terminadas.
    """
    propia = momento_de_subida(trabajo)
    if propia is None:
        return False
    for otro in ejecucion:
        if otro is trabajo or str(otro.carpeta) == str(trabajo.carpeta):
            continue
        manifiesto = otro.manifiesto
        if not (
            manifiesto.etapa_hecha("verificar")
            or manifiesto.etapa_hecha("completar")
        ):
            continue
        suya = momento_de_subida(otro)
        if suya is None or not _mismo_reloj(suya, propia):
            continue
        if suya > propia:
            return True
    return False


def busqueda_amplia_sin_hallar(trabajo: "Trabajo") -> bool:
    """Si ya se recorrio la cola entera de AirVault sin encontrar la carga.

    La busqueda amplia no va por el titulo esperado: mira todos los lotes
    publicados, incluido el ``Empty-Batch`` sin nombre, y los contrasta por
    cantidad de paginas y por los Log Page Number de dentro. Que falle no
    deja ningun nombre bajo el que la carga pueda estar escondida, asi que
    no hay nada mas que buscar ni que esperar: falta el archivo.
    """
    return int(trabajo.manifiesto.busquedas_amplias_sin_hallar or 0) > 0


def subida_perdida(
    parte: EstadoParte, ejecucion: Sequence["Trabajo"] = ()
) -> bool:
    """Si una carga que Quick Upload acepto se puede dar por no publicada.

    Tres motivos, y basta con uno: la cola entera de AirVault no la tiene
    bajo ningun nombre, las partes siguientes ya se indexaron, o el archivo
    lleva subido mas tiempo del que AirVault tarda en publicar. Solo se
    pregunta de una carga que ya agoto las revisiones de identidad, que es
    lo que significa ``BUSCANDO``.
    """
    if parte.estado != BUSCANDO:
        return False
    trabajo = parte.trabajo
    if busqueda_amplia_sin_hallar(trabajo):
        return True
    if subida_rebasada(trabajo, ejecucion):
        return True
    return subida_estancada(trabajo, espera_de_reenvio(trabajo))


def reenvio_pendiente(
    parte: EstadoParte, ejecucion: Sequence["Trabajo"] = ()
) -> bool:
    """Si esta carga ya se puede volver a enviar sin pedirlo a mano.

    Basta con darla por perdida. No hay un numero de intentos tras el cual
    el programa se rinda: un batch que no llego a AirVault no se arregla
    por dejar de mandarlo. Lo que evita insistir contra una cola lenta es
    que cada reenvio exige mas espera que el anterior
    (:func:`espera_de_reenvio`).
    """
    return subida_perdida(parte, ejecucion)


def partes_por_subir(estados: Sequence[EstadoParte]) -> List["Trabajo"]:
    """Trabajos que hay que mandar a Quick Upload, ahora o de nuevo.

    Son los que nunca llegaron a subirse y los que se dieron por perdidos.
    Vive aqui, y no en la ventana, porque la comprobacion periodica y el
    boton tienen que decidir exactamente lo mismo: si la lista se calculara
    en dos sitios, uno de los dos acabaria dejando archivos parados para
    siempre.

    Recibe la ejecucion entera porque dar una carga por perdida depende de
    las demas: son las partes siguientes, ya indexadas, las que demuestran
    que AirVault paso de largo.
    """
    ejecucion = [parte.trabajo for parte in estados]
    return [
        parte.trabajo
        for parte in estados
        if parte.estado == SIN_SUBIR or reenvio_pendiente(parte, ejecucion)
    ]


def subir_y_descubrir_partes(
    trabajos: Sequence["Trabajo"],
    sesion,
    cliente,
    esperar: bool = True,
    dormir: Callable[[float], None] = time.sleep,
    avisar: Optional[Aviso] = None,
    al_encontrar: Optional[Callable[["Trabajo", Sequence["Trabajo"]], None]] = None,
) -> None:
    """Sube todas las partes y solo despues empieza a ubicarlas.

    ``al_encontrar`` puede iniciar trabajo sobre un batch resuelto, pero se
    invoca unicamente cuando ya terminaron todas las subidas.
    """
    for trabajo in trabajos:
        cabeza = _prefijo(trabajo)

        def propio(texto: str, hechas: int, total: int, cabeza: str = cabeza) -> None:
            if avisar is not None:
                avisar(f"{cabeza}{texto}", hechas, total)

        trabajo.subir(sesion, avisar=propio if avisar else None, cliente=cliente)

    descubrir_partes(
        trabajos,
        cliente,
        esperar=esperar,
        dormir=dormir,
        avisar=avisar,
        al_encontrar=al_encontrar,
    )


def descubrir_partes(
    trabajos: Sequence["Trabajo"],
    cliente,
    esperar: bool = True,
    dormir: Callable[[float], None] = time.sleep,
    avisar: Optional[Aviso] = None,
    al_encontrar: Optional[Callable[["Trabajo", Sequence["Trabajo"]], None]] = None,
) -> None:
    """Ubica batches ya subidos, sin confundir varios ``Empty-Batch``.

    Se recorren en orden inverso porque la instantanea de una subida posterior
    puede contener los batches de las anteriores. Cada ID resuelto queda fuera
    de los candidatos de las instantaneas previas.
    """
    lotes = list(cliente.listar_lotes())
    _reconciliar_batches(trabajos, cliente, lotes, avisar, cache={})
    ids_posteriores: List[str] = []
    for trabajo in reversed(list(trabajos)):
        manifiesto = trabajo.manifiesto
        if ids_posteriores and manifiesto.lotes_previos:
            conocidos = {
                str(batch_id).strip().upper()
                for batch_id in manifiesto.lotes_previos
            }
            agregados = [
                batch_id
                for batch_id in ids_posteriores
                if str(batch_id).strip().upper() not in conocidos
            ]
            if agregados:
                manifiesto.lotes_previos.extend(agregados)
                trabajo.guardar()
        if manifiesto.batch_id:
            ids_posteriores.append(manifiesto.batch_id)
            if al_encontrar is not None:
                al_encontrar(trabajo, trabajos)
            continue
        cabeza = _prefijo(trabajo)

        def propio(texto: str, hechas: int, total: int, cabeza: str = cabeza) -> None:
            if avisar is not None:
                avisar(f"{cabeza}{texto}", hechas, total)

        trabajo.descubrir(cliente, esperar, dormir, propio if avisar else None)
        if manifiesto.batch_id:
            ids_posteriores.append(manifiesto.batch_id)
        if al_encontrar is not None:
            al_encontrar(trabajo, trabajos)


def trabajos_preparados(
    config: AirVaultConfig,
    carpeta: Path | str,
    csv: Path | str,
) -> List["Trabajo"]:
    """Manifiestos de esta ejecucion que hay en la carpeta, tal como estan.

    No exige que formen una entrega completa: es lo que hace falta antes de
    volver a repartir, cuando justamente lo que se quiere saber es que hay
    ya subido y que falta. Para presentarlos en la ventana esta
    :func:`cargar_partes`, que si comprueba que el juego este entero.
    """
    carpeta = Path(carpeta)
    carpetas = [carpeta] if manifiestos.existe(carpeta) else []
    if carpeta.is_dir():
        carpetas.extend(
            hija
            for hija in sorted(carpeta.iterdir())
            if hija.is_dir() and manifiestos.existe(hija)
        )
    objetivo = Path(csv).resolve()
    trabajos: List["Trabajo"] = []
    for propia in carpetas:
        try:
            trabajo = Trabajo.cargar(config, propia)
        except (OSError, ValueError):
            continue
        if not _reubicar_trabajo(trabajo, objetivo, carpeta):
            continue
        trabajos.append(trabajo)
    return trabajos


def cargar_partes(
    config: AirVaultConfig,
    carpeta: Path | str,
    csv: Path | str,
) -> List["Trabajo"]:
    """Trabajos ya preparados de una ejecucion, sin volver a prepararlos.

    Es lo que permite retomar una ejecucion subida ayer: los manifiestos
    dicen en que quedo cada parte, asi que la ventana puede enseñar sus
    batches sin tocar la red ni volver a escribir nada en el disco.

    Devuelve la lista vacia si falta el manifiesto de alguna parte: media
    ejecucion cargada seria peor que ninguna, porque las partes que
    faltaran parecerian no existir.
    """
    partes_originales = partes_de_corrida(csv)
    if not partes_originales:
        return []
    carpeta = Path(carpeta)
    trabajos = trabajos_preparados(config, carpeta, csv)
    if not trabajos:
        return []

    def grupo(revisar: bool) -> Optional[List["Trabajo"]]:
        suyos = [t for t in trabajos if t.manifiesto.solo_subir is revisar]
        if not suyos:
            return []
        if revisar and len(suyos) == 1 and suyos[0].manifiesto.paginas_por_batch == 0:
            # Antes del limite configurable, el unico REVISAR heredaba la
            # numeracion de los automaticos. Se acepta para poder retomar
            # trabajos ya subidos con ese formato antiguo.
            return suyos
        esperadas = suyos[0].manifiesto.partes
        numeros = {t.manifiesto.parte for t in suyos}
        if any(t.manifiesto.partes != esperadas for t in suyos) or numeros != set(
            range(1, esperadas + 1)
        ):
            return None
        return sorted(suyos, key=lambda t: t.manifiesto.parte)

    automaticos = grupo(False)
    revisar = grupo(True)
    if automaticos is None or revisar is None:
        return []
    habia_automaticos = any(not parte.revisar for parte in partes_originales)
    habia_revisar = any(parte.revisar for parte in partes_originales)
    if habia_automaticos != bool(automaticos) or habia_revisar != bool(revisar):
        # Una preparacion cortada no se presenta como una entrega completa.
        return []
    return list(automaticos) + list(revisar)


def _reubicar_trabajo(
    trabajo: "Trabajo",
    csv: Path,
    carpeta_job: Path,
) -> bool:
    """Recupera un manifiesto cuando toda la carpeta portable se movio.

    Los manifiestos antiguos guardaban rutas absolutas. La identidad estable
    es el nombre de la ejecucion y del archivo, no la letra de unidad ni el
    usuario de Windows en el que se creo.
    """
    manifiesto = trabajo.manifiesto
    csv_guardado = Path(manifiesto.csv_origen or "")
    misma_ruta = csv_guardado == csv
    misma_ejecucion = (
        csv_guardado.name.casefold() == csv.name.casefold()
        and csv_guardado.parent.parent.name.casefold()
        == csv.parent.parent.name.casefold()
    )
    if not (misma_ruta or misma_ejecucion):
        return False

    pdf_guardado = Path(manifiesto.pdf_origen or "")
    candidatos = [pdf_guardado]
    if pdf_guardado.name:
        candidatos.extend(
            (
                carpeta_de_corrida(csv) / pdf_guardado.name,
                carpeta_job / "cargas" / pdf_guardado.name,
            )
        )
    pdf_actual = next((ruta.resolve() for ruta in candidatos if ruta.is_file()), None)
    if pdf_actual is None:
        return False

    if csv_guardado != csv or pdf_guardado != pdf_actual:
        manifiesto.csv_origen = str(csv)
        manifiesto.pdf_origen = str(pdf_actual)
        trabajo.guardar()
    return True


def cargar_trabajos_pendientes(
    config: AirVaultConfig,
    carpeta_raiz: Path | str,
) -> List["Trabajo"]:
    """Recupera batches creados por la aplicacion que aun requieren trabajo.

    Solo se confia en manifiestos propios. Se incluyen primero los que todavía
    no se han subido, siempre que conserven su PDF local: elegir otra ejecución
    en la ventana no debe esconderlos ni dejarlos sin turno. Los batches de
    REVISAR tambien se recuperan; los completados ya salieron de Web Index.
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
        if completar and completar.estado is EstadoEtapa.HECHA:
            continue
        subida_confirmada = bool(
            subida
            and subida.estado
            in (
                EstadoEtapa.HECHA,
                EstadoEtapa.OMITIDA,
                EstadoEtapa.EN_CURSO,
            )
        )
        if not subida_confirmada and not Path(manifiesto.pdf_origen).is_file():
            # Sin el archivo no hay nada que Quick Upload pueda enviar. Los
            # ya subidos sí se conservan aunque el portable se haya movido,
            # porque todavía pueden verificarse o indexarse por su ID.
            continue
        trabajos.append(trabajo)
    return sorted(
        trabajos,
        key=lambda t: (
            t.manifiesto.etapa_hecha("subir"),
            t.manifiesto.creado,
            str(t.carpeta).casefold(),
        ),
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
        subida_hecha = bool(
            subida
            and subida.estado
            in (
                EstadoEtapa.HECHA,
                EstadoEtapa.OMITIDA,
            )
        )
        if not subida_hecha:
            manifiesto.batch_id = None
            # Reiniciar es una orden expresa: devuelve los reenvios que la
            # automatizacion hubiera gastado sobre este mismo archivo.
            manifiesto.reenvios = 0
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


def _cancelado_de(trabajo: "Trabajo") -> EstadoParte:
    """Un batch que alguien saco de la cola a mano.

    No se sube, no se busca en AirVault y no se indexa. Lo que ya estuviera
    hecho se conserva tal cual: cancelar es dejar de trabajar en el, no
    deshacer nada.
    """
    return EstadoParte(
        trabajo,
        CANCELADO,
        "fuera de la cola hasta que se reanude",
    )


def _cierre_de(trabajo: "Trabajo") -> EstadoParte:
    """Como quedo un batch ya cerrado, y por mano de quien.

    Un batch que cerro el programa al terminar de indexarlo no pidio nada a
    nadie: se distingue del que ya estaba cerrado cuando se encontro, que
    cerro otra persona o una version anterior del programa.
    """
    if trabajo.manifiesto.completado_automatico:
        return EstadoParte(
            trabajo,
            AUTOCOMPLETADO,
            "lo cerro el programa al terminar de indexarlo",
        )
    return EstadoParte(trabajo, COMPLETADO, "cerrado en AirVault")


def estado_local(trabajo: "Trabajo") -> EstadoParte:
    """En que va una parte segun su manifiesto, sin preguntar a AirVault.

    Sirve para pintar la lista en cuanto se elige una ejecucion. Lo que no
    puede saber es si el servidor ya termino de procesar el batch: eso solo
    lo dice :func:`comprobar_partes`, que si pregunta.
    """
    manifiesto = trabajo.manifiesto
    completar = manifiesto.etapas.get("completar")
    if completar and completar.estado is EstadoEtapa.HECHA:
        return _cierre_de(trabajo)
    if manifiesto.cancelado:
        return _cancelado_de(trabajo)
    if not manifiesto.etapa_hecha("subir"):
        subir = manifiesto.etapas.get("subir")
        detalle = (
            subir.detalle
            if subir and subir.estado is EstadoEtapa.ERROR and subir.detalle
            else "todavía sin subir"
        )
        return EstadoParte(trabajo, SIN_SUBIR, detalle)
    verificar = manifiesto.etapas.get("verificar")
    if verificar and verificar.estado is EstadoEtapa.HECHA:
        return EstadoParte(trabajo, INDEXADO, verificar.detalle)
    if verificar and verificar.estado is EstadoEtapa.ERROR:
        return EstadoParte(trabajo, INCOMPLETO, verificar.detalle)
    if manifiesto.solo_subir and manifiesto.batch_id:
        return EstadoParte(
            trabajo, SOLO_REVISAR, "subido; falta escribir los datos disponibles"
        )
    return EstadoParte(trabajo, BUSCANDO, "subido; falta comprobar")


def _nombre_embebido_empty_batch(cliente, lote: ResumenLote) -> str:
    """Lee el Batch Name que Quick Upload dejó dentro de la primera página."""
    abierto = False
    try:
        cliente.abrir_lote(lote.batch_id)
        abierto = True
        pagina = cliente.leer_pagina(lote.batch_id, 1)
        return str(
            pagina.valores.get(CAMPO_BATCH_NAME)
            or pagina.columnas.get("Batch Name")
            or pagina.columnas.get("C_BatchName")
            or ""
        ).strip()
    except Exception as exc:  # noqa: BLE001 - se reintenta en el sondeo
        logger.info(
            "Todavia no se pudo leer el Batch Name interno de {}: {}",
            lote.batch_id,
            exc,
        )
        return ""
    finally:
        if abierto:
            try:
                cliente.cerrar_lote(lote.batch_id)
            except Exception as exc:  # noqa: BLE001 - no oculta la lectura
                logger.info(
                    "No se pudo soltar {} despues de identificarlo: {}",
                    lote.batch_id,
                    exc,
                )


def _registros_de_huella(
    manifiesto: Manifiesto, maximo: int = 7
) -> List[tuple[int, Registro]]:
    """Paginas distribuidas por todo el batch con Log Page Number local."""
    disponibles = [
        (registro.pagina_batch or registro.seq, registro)
        for registro in manifiesto.bitacoras()
        if normalizar_log_number(registro.log_number)
    ]
    if len(disponibles) <= maximo:
        return disponibles
    indices = {
        round(posicion * (len(disponibles) - 1) / (maximo - 1))
        for posicion in range(maximo)
    }
    return [disponibles[indice] for indice in sorted(indices)]


def _coincide_huella_ocr(
    trabajo: "Trabajo", cliente, lote: ResumenLote
) -> tuple[bool, int, bool]:
    """Contrasta el OCR remoto con logs locales repartidos por el batch.

    Los valores vacios no cuentan en contra: AirVault a veces no coloca el
    resultado del OCR. Un valor presente y distinto si descarta al candidato.
    Se exigen varias coincidencias cuando el batch las permite, de modo que un
    solo log repetido en otra carga no autorice un renombrado.
    """
    muestras = _registros_de_huella(trabajo.manifiesto)
    if not muestras:
        return False, 0, False
    abierto = False
    coincidencias = 0
    apoyos = 0
    try:
        cliente.abrir_lote(lote.batch_id)
        abierto = True
        for pagina, registro in muestras:
            remota = cliente.leer_pagina(lote.batch_id, pagina)
            log_remoto = normalizar_log_number(
                remota.valores.get(CAMPO_LOG_NUMBER, "")
            )
            if not log_remoto:
                continue
            if log_remoto != normalizar_log_number(registro.log_number):
                return False, 0, True
            coincidencias += 1
            matricula_remota = normalizar_matricula(
                remota.valores.get(CAMPO_MATRICULA, "")
            )
            if (
                matricula_remota
                and matricula_remota
                == normalizar_matricula(registro.matricula)
            ):
                apoyos += 1
            fecha_remota = str(
                remota.valores.get(CAMPO_END_DATE, "") or ""
            ).strip()
            if fecha_remota and fecha_remota == fecha_airvault(registro.fecha):
                apoyos += 1
    except Exception as exc:  # noqa: BLE001 - el siguiente sondeo reintenta
        logger.info(
            "Todavia no se pudo contrastar la huella OCR de {}: {}",
            lote.batch_id,
            exc,
        )
        return False, 0, False
    finally:
        if abierto:
            try:
                cliente.cerrar_lote(lote.batch_id)
            except Exception as exc:  # noqa: BLE001 - no oculta la huella
                logger.info(
                    "No se pudo soltar {} despues de leer su huella: {}",
                    lote.batch_id,
                    exc,
                )
    minimo = min(3, len(muestras))
    suficiente = coincidencias >= minimo or (
        coincidencias >= 1 and apoyos >= 2
    )
    # Un log adicional siempre pesa mas que todos los apoyos posibles de
    # matricula y fecha; estos solo desempatan huellas con los mismos logs.
    return suficiente, coincidencias * 100 + apoyos, False


def _lote_por_identidad_y_contenido(
    trabajo: "Trabajo",
    cliente,
    lotes: Sequence[ResumenLote],
    avisar: Optional[Aviso] = None,
    cache: Optional[dict[str, str]] = None,
    excluir_ids: Optional[set[str]] = None,
) -> Optional[ResumenLote]:
    """Encuentra el batch por paginas, nombre interno y huella OCR.

    Se aplica también cuando el título visible ya parece correcto. Un nombre
    nunca autoriza por sí solo un batch con páginas distintas ni prevalece
    sobre un Log Page Number que contradice el manifiesto. Si AirVault no
    publicó ningún OCR, el nombre completo o el Batch Name interno sirven de
    respaldo junto con la cantidad exacta de páginas.
    """
    manifiesto = trabajo.manifiesto
    compatibles = manifiesto.cantidades_paginas_compatibles()
    nuevos = recien_llegados(
        lotes, manifiesto.lotes_previos, manifiesto.repo_id
    )
    ids_nuevos = {lote.batch_id.strip().upper() for lote in nuevos}
    excluir_ids = {str(valor).strip().upper() for valor in excluir_ids or set()}
    cache = cache if cache is not None else {}
    esperado = normalizar_nombre(manifiesto.nombre_batch)
    candidatos: List[tuple[int, ResumenLote, str]] = []
    for lote in lotes:
        clave = lote.batch_id.strip().upper()
        if (
            clave in excluir_ids
            or (lote.repo_id and lote.repo_id != manifiesto.repo_id)
            or lote.paginas not in compatibles
        ):
            continue
        nombre_visible = _nombre_visible_compatible(
            lote.nombre, manifiesto.nombre_batch
        )
        # Un batch con otro título solo puede ser el nuestro si apareció
        # después de esta subida. Así no se inspeccionan ni renombran cargas
        # antiguas ajenas que casualmente tengan la misma cantidad.
        if not nombre_visible and clave not in ids_nuevos:
            continue
        if clave not in cache:
            cache[clave] = _nombre_embebido_empty_batch(cliente, lote)
        interno = normalizar_nombre(cache[clave])
        nombre_interno = bool(interno and interno == esperado)

        # El Batch Name interno salio del PDF que subio este manifiesto. Con
        # la cantidad exacta de paginas identifica el batch sin abrir otras
        # siete paginas OCR por cada combinacion posible. Ademas de ser mas
        # rapido, evita que logs repetidos entre partes pesen mas que el
        # nombre que viajo dentro del propio archivo.
        if interno:
            if not nombre_interno:
                continue
            candidatos.append((5000, lote, "su Batch Name interno coincide"))
            continue

        coincide, puntaje_ocr, contradice = _coincide_huella_ocr(
            trabajo, cliente, lote
        )
        if contradice:
            # Esta es la protección que antes faltaba para los batches cuyo
            # título ya era correcto: contenido distinto significa otro ID.
            continue

        if coincide:
            puntaje = 3000 + puntaje_ocr
            motivo = (
                f"su huella OCR coincide en {puntaje_ocr // 100} "
                "Log Page Number distribuidos por el batch"
            )
        elif nombre_visible:
            # AirVault a veces no coloca el resultado OCR. En ese caso se
            # conserva el batch que ya tiene el título completo, pero solo
            # después de comprobar su cantidad y de no hallar contradicción.
            puntaje = 1000 + puntaje_ocr
            motivo = "su nombre completo y sus páginas coinciden"
        else:
            continue
        candidatos.append((puntaje, lote, motivo))

    if not candidatos:
        return None
    candidatos.sort(key=lambda elemento: elemento[0], reverse=True)
    if len(candidatos) > 1 and candidatos[0][0] == candidatos[1][0]:
        return None
    _puntaje, lote, _motivo = candidatos[0]
    if avisar is not None:
        if normalizar_nombre(lote.nombre) == "empty batch":
            texto = f"Batch {lote.batch_id}: Empty-Batch detectado"
        elif not _nombre_visible_compatible(
            lote.nombre, manifiesto.nombre_batch
        ):
            actual = str(lote.nombre or "(sin nombre)").strip()
            texto = (
                f"Batch {lote.batch_id}: nombre incorrecto «{actual}»"
            )
        else:
            texto = f"Batch {lote.batch_id}: identidad confirmada"
        avisar(texto, 0, 0)
    return lote


def _empty_batch_por_nombre_embebido(
    trabajo: "Trabajo",
    cliente,
    lotes: Sequence[ResumenLote],
    avisar: Optional[Aviso] = None,
    cache: Optional[dict[str, str]] = None,
) -> Optional[ResumenLote]:
    """Compatibilidad interna para el antiguo nombre del identificador."""
    return _lote_por_identidad_y_contenido(
        trabajo, cliente, lotes, avisar=avisar, cache=cache
    )


def _ubicar(
    trabajo: "Trabajo",
    cliente,
    lotes: Sequence[ResumenLote],
    cache: Optional[dict[str, str]] = None,
    avisar: Optional[Aviso] = None,
) -> Optional[ResumenLote]:
    """Verifica la parte por nombre, páginas y contenido, y actualiza su ID.

    Devuelve ``None`` mientras AirVault no lo haya sacado, que no es un
    fallo: un batch recien subido tarda en cruzar su procesamiento. El nombre
    El mismo contraste se hace aunque el nombre visible ya sea exacto. La
    diferencia contra la cola nunca basta por si sola para renombrar.
    """
    manifiesto = trabajo.manifiesto
    lote = _lote_por_identidad_y_contenido(
        trabajo, cliente, lotes, avisar=avisar, cache=cache
    )
    if lote is None:
        if manifiesto.batch_id:
            manifiesto.batch_id = None
            manifiesto.etapas["descubrir"] = Etapa()
            trabajo.guardar()
        return None
    try:
        trabajo.anotar_lote(cliente, lote, avisar)
        return lote
    except ErrorDeCorrida as exc:
        if (
            manifiesto.batch_id
            and str(manifiesto.batch_id).strip().upper()
            != str(lote.batch_id).strip().upper()
        ):
            # El ID anterior ya demostro pertenecer a otro manifiesto. Si la
            # correccion del ID nuevo no se pudo confirmar, no se conserva
            # una asignacion vieja que podria llegar al indexado.
            manifiesto.batch_id = None
            manifiesto.etapas["descubrir"] = Etapa()
            trabajo.guardar()
        logger.info(
            "La confirmacion automatica de {} seguira en el proximo sondeo: {}",
            lote.batch_id,
            exc,
        )
        return None


def _es_nombre_temporal(nombre: str) -> bool:
    """Nombres provisionales que AirVault usa antes del renombrado."""
    return normalizar_nombre(nombre) in {"empty batch", "index batch"}


def _nombre_visible_compatible(nombre: str, esperado: str) -> bool:
    """Titulo completo, incluido el sufijo de usuario que agrega AirVault."""
    if normalizar_nombre(nombre) == normalizar_nombre(esperado):
        return True
    partes = str(nombre or "").rsplit(" - ", 1)
    return bool(
        len(partes) == 2
        and "@" in partes[1]
        and normalizar_nombre(partes[0]) == normalizar_nombre(esperado)
    )


def _es_nombre_provisional(nombre: str, esperado: str) -> bool:
    """Nombre temporal o prefijo truncado del titulo completo esperado."""
    if _es_nombre_temporal(nombre):
        return True
    actual = normalizar_nombre(nombre)
    objetivo = normalizar_nombre(esperado)
    return bool(
        actual
        and actual != objetivo
        and objetivo.startswith(actual + " ")
    )


def _hay_candidato_provisional(
    trabajo: "Trabajo",
    cliente,
    lotes: Sequence[ResumenLote],
    cache: Optional[dict[str, str]] = None,
) -> bool:
    """Hay una carga remota plausible que aun no autoriza renombrar.

    Su presencia evita que el boton de revision la reenvie solo por antigua:
    primero se sigue intentando leer su identidad interna.
    """
    manifiesto = trabajo.manifiesto
    compatibles = manifiesto.cantidades_paginas_compatibles()
    for lote in recien_llegados(
        lotes, manifiesto.lotes_previos, manifiesto.repo_id
    ):
        if lote.paginas not in compatibles or not _es_nombre_provisional(
            lote.nombre, manifiesto.nombre_batch
        ):
            continue
        clave = lote.batch_id.strip().upper()
        if cache is None:
            nombre_interno = _nombre_embebido_empty_batch(cliente, lote)
        else:
            if clave not in cache:
                cache[clave] = _nombre_embebido_empty_batch(cliente, lote)
            nombre_interno = cache[clave]
        interno = normalizar_nombre(nombre_interno)
        esperado = normalizar_nombre(manifiesto.nombre_batch)
        if interno:
            if interno == esperado:
                return True
            continue
        coincide, _puntaje, contradice = _coincide_huella_ocr(
            trabajo, cliente, lote
        )
        if coincide or not contradice:
            return True
    return False


def _candidatos_provisionales_descuadrados(
    trabajo: "Trabajo", lotes: Sequence[ResumenLote]
) -> List[ResumenLote]:
    """Cargas plausibles cuyo total remoto no corresponde al PDF local."""
    manifiesto = trabajo.manifiesto
    compatibles = manifiesto.cantidades_paginas_compatibles()
    nuevos = {
        lote.batch_id.strip().upper()
        for lote in recien_llegados(
            lotes, manifiesto.lotes_previos, manifiesto.repo_id
        )
    }
    return [
        lote
        for lote in lotes
        if (not lote.repo_id or lote.repo_id == manifiesto.repo_id)
        and lote.paginas > max(compatibles)
        and (
            _nombre_visible_compatible(lote.nombre, manifiesto.nombre_batch)
            or (
                lote.batch_id.strip().upper() in nuevos
                and _es_nombre_provisional(
                    lote.nombre, manifiesto.nombre_batch
                )
            )
        )
    ]


def _candidatos_con_contenido_contradictorio(
    trabajo: "Trabajo", cliente, lotes: Sequence[ResumenLote]
) -> List[ResumenLote]:
    """Batches con nombre y páginas correctos, pero Log Page Number ajenos."""
    manifiesto = trabajo.manifiesto
    compatibles = manifiesto.cantidades_paginas_compatibles()
    encontrados: List[ResumenLote] = []
    for lote in lotes:
        if (
            (lote.repo_id and lote.repo_id != manifiesto.repo_id)
            or lote.paginas not in compatibles
            or not _nombre_visible_compatible(
                lote.nombre, manifiesto.nombre_batch
            )
        ):
            continue
        _coincide, _puntaje, contradice = _coincide_huella_ocr(
            trabajo, cliente, lote
        )
        if contradice:
            encontrados.append(lote)
    return encontrados


def _candidatos_con_paginas_parciales(
    trabajo: "Trabajo", lotes: Sequence[ResumenLote]
) -> List[ResumenLote]:
    """Batches con título correcto que AirVault aún está terminando de armar."""
    manifiesto = trabajo.manifiesto
    minimo = min(manifiesto.cantidades_paginas_compatibles())
    return [
        lote
        for lote in lotes
        if (not lote.repo_id or lote.repo_id == manifiesto.repo_id)
        and lote.paginas < minimo
        and _nombre_visible_compatible(
            lote.nombre, manifiesto.nombre_batch
        )
    ]


def _reconciliar_batches(
    trabajos: Sequence["Trabajo"],
    cliente,
    lotes: Sequence[ResumenLote],
    avisar: Optional[Aviso] = None,
    cache: Optional[dict[str, str]] = None,
    fallidos: Optional[set[str]] = None,
) -> int:
    """Identifica, corrige y confirma todos los nombres de la entrega.

    La instantanea previa acota candidatos. El Batch Name interno o la huella
    OCR exacta decide; orden de llegada y cantidad de paginas nunca deciden
    solos.
    """
    usados = {
        str(trabajo.manifiesto.batch_id).strip().upper()
        for trabajo in trabajos
        if trabajo.manifiesto.batch_id
    }
    pendientes = [
        trabajo
        for trabajo in trabajos
        if not trabajo.manifiesto.batch_id
        and trabajo.manifiesto.lotes_previos
        and trabajo.manifiesto.etapa_hecha("subir")
        and not trabajo.manifiesto.etapa_hecha("completar")
    ]
    renombrados = 0
    nombres_embebidos = cache if cache is not None else {}
    while pendientes:
        elegido = next(
            (
                (trabajo, por_contenido)
                for trabajo in pendientes
                if (
                    por_contenido := _lote_por_identidad_y_contenido(
                        trabajo,
                        cliente,
                        lotes,
                        avisar,
                        nombres_embebidos,
                        usados,
                    )
                ) is not None
            ),
            None,
        )
        if elegido is None:
            break
        trabajo, lote = elegido
        requiere_renombre = not _nombre_visible_compatible(
            lote.nombre, trabajo.manifiesto.nombre_batch
        )
        try:
            trabajo.anotar_lote(cliente, lote, avisar)
        except ErrorDeCorrida as exc:
            if fallidos is not None:
                fallidos.add(str(trabajo.carpeta))
            if avisar is not None:
                avisar(
                    f"Batch {lote.batch_id}: nombre no confirmado; "
                    "se reintentará",
                    0,
                    0,
                )
            logger.info(
                "La correccion automatica de {} queda para el proximo sondeo: {}",
                lote.batch_id,
                exc,
            )
            # Un nombre que AirVault aun no confirma no debe frenar la
            # correccion de los otros batches. Se reserva este ID durante la
            # ronda y se deja su propio trabajo para el siguiente sondeo.
            usados.add(lote.batch_id.strip().upper())
            pendientes.remove(trabajo)
            continue
        usados.add(lote.batch_id.strip().upper())
        pendientes.remove(trabajo)
        if requiere_renombre:
            renombrados += 1
    return renombrados


def _revisar_ids_asignados(
    trabajos: Sequence["Trabajo"],
    cliente,
    lotes: Sequence[ResumenLote],
    cache: dict[str, str],
    avisar: Optional[Aviso] = None,
) -> tuple[bool, set[str], set[str]]:
    """Corrige IDs cruzados antes de repartir los que siguen pendientes.

    Un manifiesto antiguo puede apuntar al ID de otra parte. Mientras ese ID
    figure como usado, el trabajo correcto no puede reclamarlo. Por eso esta
    revision ocurre antes de :func:`_reconciliar_batches` y comparte las
    lecturas del Batch Name interno con el resto del mismo sondeo.
    """
    cambiados = False
    confirmados: set[str] = set()
    fallidos: set[str] = set()
    por_id = {
        lote.batch_id.strip().upper(): lote
        for lote in lotes
    }
    for trabajo in trabajos:
        manifiesto = trabajo.manifiesto
        anterior = str(manifiesto.batch_id or "").strip()
        if not anterior or manifiesto.etapa_hecha("completar"):
            continue
        remoto_anterior = por_id.get(anterior.upper())
        nombre_incorrecto = bool(
            remoto_anterior
            and not _nombre_visible_compatible(
                remoto_anterior.nombre, manifiesto.nombre_batch
            )
        )
        ubicado = _ubicar(trabajo, cliente, lotes, cache, avisar)
        actual = str(manifiesto.batch_id or "").strip()
        if ubicado is not None and actual:
            confirmados.add(str(trabajo.carpeta))
        elif actual:
            fallidos.add(str(trabajo.carpeta))
        cambiados = cambiados or actual.upper() != anterior.upper()
        cambiados = cambiados or nombre_incorrecto
    return cambiados, confirmados, fallidos


def _reconciliar_empty_batches(
    trabajos: Sequence["Trabajo"],
    cliente,
    lotes: Sequence[ResumenLote],
    avisar: Optional[Aviso] = None,
) -> int:
    """Alias interno conservado para llamadas y pruebas antiguas."""
    return _reconciliar_batches(trabajos, cliente, lotes, avisar)


def _registrar_revision_sin_identificar(
    trabajo: "Trabajo", ahora: Optional[datetime] = None
) -> tuple[int, bool]:
    """Cuenta revisiones completas y arranca después la espera de resubida."""
    manifiesto = trabajo.manifiesto
    if manifiesto.espera_reenvio_desde:
        return max(
            INTENTOS_IDENTIFICACION_ANTES_DE_ESPERA,
            int(manifiesto.intentos_identificacion or 0),
        ), True
    intentos = min(
        INTENTOS_IDENTIFICACION_ANTES_DE_ESPERA,
        max(0, int(manifiesto.intentos_identificacion or 0)) + 1,
    )
    manifiesto.intentos_identificacion = intentos
    esperando = intentos >= INTENTOS_IDENTIFICACION_ANTES_DE_ESPERA
    if esperando:
        manifiesto.espera_reenvio_desde = (ahora or datetime.now()).isoformat(
            timespec="seconds"
        )
    trabajo.guardar()
    return intentos, esperando


def edad_de_la_carga(
    trabajo: "Trabajo", ahora: Optional[datetime] = None
) -> Optional[float]:
    """Segundos desde que se subio este archivo, o ``None`` si no se sabe.

    La fecha de la subida es la del manifiesto; si esa carga nunca llego a
    registrarse se usa la de preparacion del trabajo, que es la hora que
    llevan el nombre del batch y los archivos de la ejecucion.
    """
    momento = momento_de_subida(trabajo) or _momento(trabajo.manifiesto.creado)
    if momento is None:
        return None
    presente = ahora or (
        datetime.now(momento.tzinfo) if momento.tzinfo else datetime.now()
    )
    if not _mismo_reloj(presente, momento):
        return None
    try:
        return (presente - momento).total_seconds()
    except (TypeError, ValueError, OverflowError):
        return None


def espera_de_reenvio(trabajo: "Trabajo") -> float:
    """Cuanto tiene que llevar sin publicarse antes de volver a mandarla.

    La primera vez es la espera configurada; con cada reenvio ya hecho, un
    multiplo mas. Es lo que sustituye al tope de reenvios: en vez de dejar
    de intentarlo (lo que dejaba el batch fuera de AirVault para siempre),
    se intenta cada vez mas espaciado.
    """
    base = max(0.0, float(trabajo.config.espera_reenvio_s))
    return base * (1 + max(0, int(trabajo.manifiesto.reenvios or 0)))


def _carga_vieja_sin_publicar(trabajo: "Trabajo") -> bool:
    """Si esta carga es lo bastante antigua como para no estar en camino.

    AirVault tarda, y un batch recien subido que aun no aparece es lo
    normal. Uno que lleva mas tiempo del que el servidor tarda en publicar
    y sigue sin estar en la cola es otra cosa: probablemente lo borraron o
    nunca entro. Es la condicion que separa los dos casos.
    """
    edad = edad_de_la_carga(trabajo)
    if edad is None:
        return False
    return edad >= espera_de_reenvio(trabajo)


def _registrar_busqueda_amplia_fallida(trabajo: "Trabajo") -> EstadoParte:
    """Anota que la cola entera no tenia esta carga y la deja por reenviar."""
    manifiesto = trabajo.manifiesto
    manifiesto.busquedas_amplias_sin_hallar = (
        int(manifiesto.busquedas_amplias_sin_hallar or 0) + 1
    )
    manifiesto.intentos_identificacion = INTENTOS_IDENTIFICACION_ANTES_DE_ESPERA
    if not manifiesto.espera_reenvio_desde:
        manifiesto.espera_reenvio_desde = datetime.now().isoformat(
            timespec="seconds"
        )
    trabajo.guardar()
    return EstadoParte(
        trabajo,
        BUSCANDO,
        "no está en AirVault con ningún nombre, ni por páginas ni por Log "
        "Page Number; se vuelve a subir",
    )


def _estado_de(
    trabajo: "Trabajo",
    cliente,
    lotes: Sequence[ResumenLote],
    cache: Optional[dict[str, str]] = None,
    lote_confirmado: Optional[ResumenLote] = None,
    identidad_fallida: bool = False,
    amplia_fallida: bool = False,
) -> EstadoParte:
    """En que va una parte, mirando la cola que se acaba de pedir."""
    manifiesto = trabajo.manifiesto
    esperadas = len(manifiesto.registros)
    completar = manifiesto.etapas.get("completar")
    if completar and completar.estado is EstadoEtapa.HECHA:
        return _cierre_de(trabajo)
    verificar = manifiesto.etapas.get("verificar")
    if verificar and verificar.estado is EstadoEtapa.HECHA:
        # Ya quedo confirmado en una ejecucion anterior. No hace falta abrir
        # la cola ni volver a buscar su nombre solo para pintar la fila de la
        # tabla; si se pide completar, se usa directamente el ID guardado.
        return EstadoParte(trabajo, INDEXADO, verificar.detalle)
    if manifiesto.cancelado:
        return _cancelado_de(trabajo)
    if identidad_fallida:
        return EstadoParte(
            trabajo,
            PROCESANDO,
            "batch identificado; falta confirmar la corrección del nombre",
        )
    subida = manifiesto.etapas.get("subir")
    subida_rastreable = bool(
        subida
        and subida.estado
        in (
            EstadoEtapa.HECHA,
            EstadoEtapa.OMITIDA,
            EstadoEtapa.EN_CURSO,
        )
    )
    # Se busca siempre, incluso si el manifiesto no alcanzo a registrar la
    # subida. Asi aparecen el batch principal sin «-numero», sus divisiones
    # y REVISAR, y un batch remoto recupera su ID en vez de volver a subirse.
    lote = lote_confirmado or _ubicar(trabajo, cliente, lotes, cache)
    if lote is None:
        if not subida_rastreable:
            return EstadoParte(trabajo, SIN_SUBIR, "no esta en AirVault")
        descuadrados = _candidatos_provisionales_descuadrados(trabajo, lotes)
        if descuadrados:
            cantidades = " o ".join(
                str(n) for n in sorted(manifiesto.cantidades_paginas_compatibles())
            )
            detalle = "; ".join(
                f"{candidato.batch_id} tiene {candidato.paginas}"
                for candidato in descuadrados[:3]
            )
            return EstadoParte(
                trabajo,
                DESCUADRADO,
                f"batch encontrado con páginas incorrectas "
                f"({detalle}); se esperaban {cantidades}. No se renombra, "
                "indexa ni vuelve a subir por su cuenta; AirVault junto "
                "dos cargas o el PDF no corresponde al índice. Si ese batch "
                "no es esta carga, mándela otra vez desde la tabla",
            )
        parciales = _candidatos_con_paginas_parciales(trabajo, lotes)
        if parciales:
            candidato = parciales[0]
            return EstadoParte(
                trabajo,
                PROCESANDO,
                f"{candidato.paginas} de "
                f"{min(manifiesto.cantidades_paginas_compatibles())} páginas",
                candidato,
            )
        contradictorios = _candidatos_con_contenido_contradictorio(
            trabajo, cliente, lotes
        )
        if contradictorios:
            detalle = "; ".join(
                f"{candidato.batch_id} tiene otro Log Page Number"
                for candidato in contradictorios[:3]
            )
            return EstadoParte(
                trabajo,
                DESCUADRADO,
                f"el nombre y las páginas coinciden, pero el contenido no "
                f"corresponde ({detalle}). No se renombra, indexa ni vuelve "
                "a subir por su cuenta; si esta carga no está, mándela otra "
                "vez desde la tabla",
            )
        if _hay_candidato_provisional(trabajo, cliente, lotes, cache):
            return EstadoParte(
                trabajo,
                PROCESANDO,
                "batch encontrado; verificando el nombre completo y su contenido",
            )
        if amplia_fallida and _carga_vieja_sin_publicar(trabajo):
            # Se recorrio la cola entera y no aparecio bajo ningun nombre, y
            # ademas la carga es vieja: no viene en camino, no esta. Contar
            # tres rondas y esperar media hora mas no va a cambiar eso, asi
            # que vuelve a Quick Upload. Una carga reciente sigue el camino
            # normal: que AirVault aun no la publique es lo esperable.
            return _registrar_busqueda_amplia_fallida(trabajo)
        intentos, esperando = _registrar_revision_sin_identificar(trabajo)
        if not esperando:
            return EstadoParte(
                trabajo,
                PROCESANDO,
                f"revisando nombres, páginas y Log Page Number "
                f"({intentos}/{INTENTOS_IDENTIFICACION_ANTES_DE_ESPERA})",
            )
        return EstadoParte(
            trabajo,
            BUSCANDO,
            f"no se identificó tras {intentos} revisiones; empezó la espera "
            "antes de permitir otra subida",
        )
    if not subida_rastreable:
        manifiesto.etapa("subir").marcar(EstadoEtapa.HECHA, "confirmado en AirVault")
        trabajo.guardar()
    cantidades_compatibles = manifiesto.cantidades_paginas_compatibles()
    if lote.paginas not in cantidades_compatibles:
        # Aparece en la cola antes de estar entero. Escribir asi correria
        # cada dato a la bitacora de al lado, asi que hasta que las
        # paginas cuadren la parte no esta lista.
        cantidades = sorted(cantidades_compatibles)
        if lote.paginas > cantidades[-1]:
            return EstadoParte(
                trabajo,
                DESCUADRADO,
                f"tiene {lote.paginas} paginas; el maximo de esta parte "
                f"es {cantidades[-1]}. No se indexa porque AirVault junto "
                "dos cargas o el PDF no corresponde al indice",
                lote,
            )
        detalle = (
            f"{lote.paginas} de {cantidades[0]} paginas"
            if len(cantidades) == 1
            else f"{lote.paginas} paginas; se esperaban "
            + " o ".join(str(n) for n in cantidades)
        )
        return EstadoParte(
            trabajo,
            PROCESANDO,
            detalle,
            lote,
        )
    if manifiesto.solo_subir:
        return EstadoParte(
            trabajo,
            SOLO_REVISAR,
            f"{esperadas} paginas para indexar y revisar",
            lote,
        )
    if verificar and verificar.estado is EstadoEtapa.ERROR:
        detalle = verificar.detalle
        if lote.bloqueado_por:
            detalle += f"; lo tiene abierto {lote.bloqueado_por}"
        return EstadoParte(trabajo, INCOMPLETO, detalle, lote)
    if lote.bloqueado_por:
        # Tomado por alguien, AirVault no lo entrega: abrirlo dejaria la
        # peticion colgada hasta que venza el tiempo limite.
        return EstadoParte(
            trabajo, TOMADO, f"lo tiene abierto {lote.bloqueado_por}", lote
        )
    return EstadoParte(trabajo, LISTO, f"{lote.paginas} paginas", lote)


def _pendiente_de_busqueda(trabajo: "Trabajo") -> bool:
    """Si la fila local aun necesita localizar algo en AirVault."""
    manifiesto = trabajo.manifiesto
    if manifiesto.cancelado or manifiesto.etapa_hecha("completar"):
        return False
    return not manifiesto.etapa_hecha("verificar")


def _subida_rastreable(trabajo: "Trabajo", tenia_batch_id: bool = False) -> bool:
    """Si una ausencia por nombre merece buscar nombres provisionales."""
    subida = trabajo.manifiesto.etapas.get("subir")
    return bool(
        tenia_batch_id
        or (
            subida
            and subida.estado
            in (
                EstadoEtapa.HECHA,
                EstadoEtapa.OMITIDA,
                EstadoEtapa.EN_CURSO,
            )
        )
    )


def comprobar_partes(
    trabajos: Sequence["Trabajo"],
    cliente,
    avisar: Optional[Aviso] = None,
) -> List[EstadoParte]:
    """Mira en que va cada parte en AirVault. No escribe nada.

    Es lo que responde «¿ya se subio?». La tabla local manda el recorrido:
    primero se buscan, en su mismo orden, los nombres que todavía faltan por
    subir o indexar. Solo si una carga ya registrada no aparece con su nombre
    se pide la cola completa para recuperar un ``Empty-Batch`` o un título
    incorrecto. Que un batch tarde en aparecer no es un fallo: AirVault lo
    procesa en su cola y puede tardar minutos u horas.
    """
    pendientes = [
        trabajo for trabajo in trabajos if _pendiente_de_busqueda(trabajo)
    ]
    lotes_por_id: dict[str, ResumenLote] = {}
    nombres_embebidos: dict[str, str] = {}
    confirmados: set[str] = set()
    fallos_identidad: set[str] = set()
    sin_hallar: set[str] = set()
    por_busqueda_amplia: List["Trabajo"] = []

    for numero, trabajo in enumerate(pendientes, start=1):
        manifiesto = trabajo.manifiesto
        nombre = manifiesto.nombre_batch
        if avisar is not None:
            avisar(
                f"Buscando {numero}/{len(pendientes)} en AirVault: «{nombre}»",
                0,
                0,
            )
        batch_id_anterior = str(manifiesto.batch_id or "").strip().upper()
        try:
            encontrados = list(cliente.listar_lotes(nombre))
        except TypeError:
            # Compatibilidad con adaptadores antiguos y clientes falsos que
            # aun no reciben el filtro de nombre.
            encontrados = list(cliente.listar_lotes())
        dirigidos = [
            lote
            for lote in encontrados
            if _nombre_visible_compatible(lote.nombre, nombre)
            or (
                batch_id_anterior
                and lote.batch_id.strip().upper() == batch_id_anterior
            )
        ]
        for lote in dirigidos:
            lotes_por_id[lote.batch_id.strip().upper()] = lote
        ubicado = _ubicar(
            trabajo,
            cliente,
            dirigidos,
            nombres_embebidos,
            avisar,
        )
        if ubicado is not None:
            clave = ubicado.batch_id.strip().upper()
            lotes_por_id[clave] = ubicado
            confirmados.add(str(trabajo.carpeta))
            continue
        if _subida_rastreable(trabajo, bool(batch_id_anterior)):
            por_busqueda_amplia.append(trabajo)

    lotes = list(lotes_por_id.values())
    if por_busqueda_amplia:
        if avisar is not None:
            avisar(
                "Los nombres pendientes no aparecieron; buscando cargas "
                "publicadas con otro nombre",
                0,
                0,
            )
        lotes = list(cliente.listar_lotes())
        corregidos = _reconciliar_batches(
            trabajos,
            cliente,
            lotes,
            avisar,
            nombres_embebidos,
            fallos_identidad,
        )
        if corregidos:
            # UpdateBatchName puede tardar en reflejarse en la cola. La
            # segunda lectura deja la tabla con el título ya confirmado.
            lotes = list(cliente.listar_lotes())
        lotes_por_id = {
            lote.batch_id.strip().upper(): lote for lote in lotes
        }
        confirmados.update(
            str(trabajo.carpeta)
            for trabajo in por_busqueda_amplia
            if trabajo.manifiesto.batch_id
        )
        # Los que siguen sin ID despues de mirar la cola entera ya no estan
        # esperando a que AirVault los publique: no llegaron.
        sin_hallar.update(
            str(trabajo.carpeta)
            for trabajo in por_busqueda_amplia
            if not trabajo.manifiesto.batch_id
        )

    return [
        _estado_de(
            trabajo,
            cliente,
            lotes,
            nombres_embebidos,
            lotes_por_id.get(
                str(trabajo.manifiesto.batch_id or "").strip().upper()
            ) if str(trabajo.carpeta) in confirmados else None,
            str(trabajo.carpeta) in fallos_identidad,
            str(trabajo.carpeta) in sin_hallar,
        )
        for trabajo in trabajos
    ]


def detectar_indexados(
    estados: Sequence[EstadoParte],
    cliente,
    avisar: Optional[Aviso] = None,
) -> List[EstadoParte]:
    """Relee los batches disponibles y registra los que ya estan en verde.

    Sirve para recuperar un batch aunque se haya borrado su manifiesto local:
    primero se lo ubica por el nombre esperado y luego se contrasta cada
    bitacora con AirVault. Si alguien lo indexo a mano, la verificacion queda
    guardada en el manifiesto nuevo y no se vuelve a escribir ni a subir.

    REVISAR conserva su flujo manual y un batch abierto por otra persona no
    se toca. Los que aun tengan paginas pendientes quedan como incompletos y
    se pueden planificar de nuevo con el estado remoto mas reciente.
    """
    detectados: List[EstadoParte] = []
    for parte in estados:
        trabajo = parte.trabajo
        if (
            parte.estado not in (LISTO, INCOMPLETO)
            or trabajo.manifiesto.solo_subir
            or (parte.lote and parte.lote.bloqueado_por)
        ):
            detectados.append(parte)
            continue
        if avisar is not None:
            avisar(
                f"{_prefijo(trabajo)}Comprobando si el batch ya esta "
                "indexado",
                0,
                0,
            )
        validas, total, _problemas = trabajo.verificar(cliente)
        completo = total > 0 and validas == total
        detectados.append(EstadoParte(
            trabajo,
            INDEXADO if completo else INCOMPLETO,
            f"{validas}/{total} en Valid",
            parte.lote,
        ))
    return detectados


def completar_partes(
    trabajos: Sequence["Trabajo"],
    cliente,
    avisar: Optional[Aviso] = None,
    automatico: bool = False,
) -> List[Tuple["Trabajo", ResultadoCompletar]]:
    """Da por terminados los batches que AirVault vaya a aceptar.

    El que tenga una sola pagina fuera de verde se queda en la cola con el
    motivo anotado. Un batch que no se deja cerrar no corta a los demas:
    son batches distintos y lo escrito en cada uno ya esta escrito.
    """
    hechos: List[Tuple["Trabajo", ResultadoCompletar]] = []
    for trabajo in trabajos:
        if trabajo.manifiesto.solo_subir:
            # El programa ya escribe todo lo confirmado. El batch se conserva
            # abierto para que una persona corrija solamente lo dudoso;
            # cerrarlo seria archivarlo sin esa revision.
            continue
        cabeza = _prefijo(trabajo)
        if avisar is not None:
            avisar(f"{cabeza}Cerrando el batch en AirVault", 0, 0)
        try:
            hechos.append(
                (trabajo, trabajo.completar(cliente, automatico=automatico))
            )
        except Exception as exc:  # noqa: BLE001 - se anota y siguen los demas
            logger.warning(
                "No se pudo cerrar el batch {}: {}",
                trabajo.manifiesto.batch_id,
                exc,
            )
            hechos.append(
                (
                    trabajo,
                    ResultadoCompletar(False, [], 0, f"AirVault no lo acepto: {exc}"),
                )
            )
    return hechos


def planificar_partes(
    trabajos: Sequence["Trabajo"],
    cliente,
    resolutor: Optional[ResolutorFlota] = None,
    sobrescribir: bool = False,
    avisar: Optional[Aviso] = None,
) -> List[Tuple[Plan, Indexador]]:
    """Calcula el plan de cada parte sin escribir nada en ninguna."""
    resolutor = resolutor or ResolutorFlota()
    planes: List[Tuple[Plan, Indexador]] = []
    for trabajo in trabajos:
        cabeza = _prefijo(trabajo)

        def propio(texto: str, hechas: int, total: int, cabeza: str = cabeza) -> None:
            if avisar is not None:
                avisar(f"{cabeza}{texto}", hechas, total)

        try:
            planes.append(
                trabajo.planificar(
                    cliente, resolutor, sobrescribir, propio if avisar else None
                )
            )
        except BaseException:
            # Una parte que falla no puede dejar tomadas las anteriores:
            # son batches distintos y ya nadie va a escribir en ellos.
            cerrar_partes(trabajos[: len(planes)], cliente)
            raise
    return planes


def cerrar_partes(trabajos: Sequence["Trabajo"], cliente) -> None:
    """Suelta en AirVault todos los batches que el recorrido dejo abiertos."""
    for trabajo in trabajos:
        trabajo.cerrar(cliente)


def indexar_partes(
    trabajos: Sequence["Trabajo"],
    planes: Sequence[Tuple[Plan, Indexador]],
    detener_en_error: bool = True,
    avisar: Optional[Aviso] = None,
) -> Resultado:
    """Escribe todas las partes y devuelve el resultado sumado.

    El avance se cuenta sobre el total de la ejecución, no sobre cada parte:
    quien mira la barra quiere saber cuanto falta para terminar, no cuanto
    falta del archivo tres.
    """
    total = sum(len(plan.escribibles) for plan, _indexador in planes)
    hechas = 0
    sumado = Resultado()
    for trabajo, (plan, indexador) in zip(trabajos, planes):
        cabeza = _prefijo(trabajo)
        arrastre = hechas

        def propio(
            texto: str,
            propias: int,
            _suyas: int,
            cabeza: str = cabeza,
            arrastre: int = arrastre,
        ) -> None:
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
        sumado.detalles.extend(f"{cabeza}{detalle}" for detalle in resultado.detalles)
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
        registro_entrega.anotar(trabajo.carpeta, [trabajo])
    return validas, total, problemas

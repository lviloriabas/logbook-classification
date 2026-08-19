"""Generación de PDFs de bitácoras ordenados y separados.

- **PDFs ordenados**: reordenan los escaneos tal cual (sin encabezados ni
  anotaciones, listos para subir a la plataforma de indexado), agrupados
  por avión (matrícula corregida) y/o mes inferido (YYYY-MM). En un
  PDF único, las secciones se ordenan por matrícula y fecha; dentro de cada
  sección las páginas siguen ``log_number`` y logpage.
- **Posibles discrepancias**: en un PDF único forman una sola sección final;
  en la salida de varios archivos se escriben en ``discrepancias.pdf``. En
  ambos casos se ordenan únicamente por logpage y no se subdividen por avión
  ni mes.
- **Revisar**: las bitácoras cuya matrícula nadie pudo confirmar (ni el OCR,
  ni el consenso del libro, ni la lista de flota). Se escriben siempre, con
  cualquier combinación de opciones: en el PDF único cierran el archivo tras
  un separador ``REVISAR`` y en la salida de varios archivos forman
  ``revisar.pdf``. No abren grupo de avión propio, porque una matrícula sin
  confirmar puesta como título se lee como un avión que existe.
- **Recortes de firmas**: volcado de las regiones de firma para auditar
  visualmente los bounding boxes.

Convenciones de nombres (rutas relativas a la carpeta de la corrida):

- ``<carpeta>.pdf``                  un único PDF: usa el mismo nombre que la
                                     carpeta de la corrida, sin separadores si
                                     no se eligen condiciones, o con páginas
                                     en blanco de matrícula/mes en grande como
                                      separadores independientes entre grupos
- ``HP-XXXXCMP.pdf``                 solo por avión (PDFs sueltos, sin carpetas)
- ``2026-JUL.pdf`` / ``sf.pdf``      solo por mes (``sf`` = sin fecha)
- ``HP-XXXXCMP_2026-JUL.pdf``        por avión y mes: ambos valores forman el
                                     nombre (``HP-XXXXCMP_sf.pdf`` sin fecha)
- ``discrepancias.pdf``              páginas con discrepancias
- ``revisar.pdf``                    páginas sin avión confirmado

Las páginas sin fecha legible van al grupo ``sin_fecha`` (archivo
``sf.pdf``) y las que no dicen de qué avión son van a «Revisar», de modo
que ninguna bitácora queda por fuera de los PDFs generados.

Exportar de nuevo nunca borra ni sobreescribe los PDFs que ya están en la
carpeta: cuando el nombre está ocupado, la copia nueva lo repite con un
sufijo numérico (``HP-XXXXCMP-2.pdf``, ``-3``…).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import pymupdf as fitz  # PyMuPDF
import numpy as np
from loguru import logger
from PIL import Image

from app.models.schemas import (
    FieldResult,
    PageResult,
    Status,
    ValidationReport,
)
from app.templates.schema import FieldType, Template
from app.utils.io import sanitize_filename, unique_path
from app.validation.discrepancias import (
    Discrepancia,
)
from app.validation.grouping import log_number
from app.vision.pdf_loader import PdfDocumentCache, copy_pdf_pages, render_page
from app.vision.signature import SIGNATURE_PAD_X, SIGNATURE_PAD_Y

SIN_MATRICULA = "sin_matricula"
# La sección «Revisar» se escribe siempre: son las bitácoras que nadie pudo
# asignar a un avión, y quedarse fuera de la entrega es peor que cualquier
# opción de separación que se haya marcado.
ETIQUETA_REVISAR = "REVISAR"
NOMBRE_PDF_REVISAR = "revisar.pdf"

_DATE_RE = re.compile(r"^\d{4}/\d{2}/\d{2}$")
_MATRICULA_RE = re.compile(r"^HP-\d{4}(CMP|WWP)$")
_MONTHS = (
    "JAN", "FEB", "MAR", "APR", "MAY", "JUN",
    "JUL", "AUG", "SEP", "OCT", "NOV", "DEC",
)
_MONTH_NUMBER = {
    "JAN": 1, "ENE": 1, "FEB": 2, "MAR": 3,
    "APR": 4, "ABR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "AGO": 8, "SEP": 9,
    "OCT": 10, "NOV": 11, "DEC": 12, "DIC": 12,
}

@dataclass(frozen=True)
class PaginaRef:
    """Referencia a una página de un reporte, con su posición original."""

    pdf_path: str
    page: PageResult
    orden: int


def iterar_paginas(reports: Sequence[ValidationReport]) -> Iterable[PaginaRef]:
    """Itera las páginas de todos los reportes en el orden del lote."""
    orden = 0
    for report in reports:
        for page in report.pages:
            yield PaginaRef(str(report.pdf_path), page, orden)
            orden += 1


def clave_avion(page: PageResult) -> str:
    """Matrícula corregida de la página, o 'sin matricula'."""
    for field in page.fields:
        if field.field_id == "matricula" and field.value:
            value = field.value.strip()
            if _MATRICULA_RE.fullmatch(value):
                return value
    return SIN_MATRICULA


def por_revisar(page: PageResult) -> bool:
    """La página no dice de qué avión es.

    Ni el OCR, ni el consenso de su libro, ni la lista de flota pudieron
    confirmar la matrícula. No hay avión al que archivarla, así que va a la
    sección «Revisar» en vez de abrir un grupo propio: un grupo con una
    matrícula sin confirmar se lee como una bitácora de un avión que existe.
    """
    return clave_avion(page) == SIN_MATRICULA


def paginas_para_revisar(
    reports: Sequence[ValidationReport],
) -> List[PaginaRef]:
    """Páginas sin avión confirmado, ordenadas por libro y logpage."""
    refs = [
        ref for ref in iterar_paginas(reports)
        if not ref.page.blank and por_revisar(ref.page)
    ]
    refs.sort(key=lambda ref: clave_orden(ref.page, ref.orden))
    return refs


def clave_mes(page: PageResult) -> str:
    """Mes de la página, incluso cuando el día no pudo determinarse."""
    if page.date and _DATE_RE.match(page.date):
        return page.date[:7].replace("/", "-")
    by_id = {field.field_id: field for field in page.fields}
    month_field = by_id.get("month")
    year_field = by_id.get("year")
    if (
        month_field is None
        or year_field is None
        or month_field.status is Status.ERROR
        or year_field.status is Status.ERROR
    ):
        return "sin_fecha"
    month_value = month_field.value or ""
    year_value = year_field.value or ""
    month = _MONTH_NUMBER.get(str(month_value).upper())
    if month is None and str(month_value).isdigit():
        numeric = int(str(month_value))
        month = numeric if 1 <= numeric <= 12 else None
    if str(year_value).isdigit() and len(str(year_value)) in (2, 4):
        year = int(str(year_value))
        if len(str(year_value)) == 2:
            year += 2000
        if month is not None and 2000 <= year <= 2100:
            return f"{year:04d}-{month:02d}"
    return "sin_fecha"


def clave_orden(page: PageResult, orden: int) -> Tuple[int, int]:
    """Clave de orden: log_number (libro + logpage) y posición original."""
    numero = log_number(page)
    return (numero if numero is not None else 1 << 30, orden)


def agrupar_paginas(
    reports: Sequence[ValidationReport],
    separar_por: Sequence[str],
    excluidas: Optional[set[Tuple[str, int]]] = None,
) -> Dict[Tuple[str, ...], List[PaginaRef]]:
    """Agrupa las páginas por las condiciones de separación elegidas.

    Args:
        reports: Reportes de validación (uno por PDF procesado).
        separar_por: Condiciones repetibles ``avion`` y/o ``mes``; vacío
            agrupa todo en una sola clave ().
        excluidas: Conjunto de (nombre del archivo, número de página) que
            NO se incluyen (páginas con discrepancia cuando se genera el
            PDF de discrepancias). Las páginas en blanco y las que no
            tienen avión confirmado siempre se excluyen: las segundas van
            a la sección «Revisar» (``paginas_para_revisar``).

    Returns:
        Diccionario clave -> lista de páginas ya ordenadas (libro, logpage).
    """
    condiciones = list(separar_por or [])
    for condicion in condiciones:
        if condicion not in ("avion", "mes"):
            raise ValueError(
                f"Condición de separación desconocida: {condicion}"
            )

    grupos: Dict[Tuple[str, ...], List[PaginaRef]] = {}
    excluidas = excluidas or set()
    for ref in iterar_paginas(reports):
        if ref.page.blank:
            continue
        if (Path(ref.pdf_path).name, ref.page.page_number) in excluidas:
            continue
        if por_revisar(ref.page):
            # Va a la sección «Revisar», no a un grupo de avión.
            continue
        if condiciones:
            clave = tuple(
                clave_avion(ref.page) if condicion == "avion"
                else clave_mes(ref.page)
                for condicion in condiciones
            )
        else:
            clave = ()
        grupos.setdefault(clave, []).append(ref)

    for refs in grupos.values():
        refs.sort(key=lambda r: clave_orden(r.page, r.orden))
    return grupos


def nombre_mes(mes: str) -> str:
    """Nombre visible del mes: ``YYYY-MMM`` o ``sf`` sin fecha legible."""
    if mes == "sin_fecha":
        return "sf"
    match = re.fullmatch(r"(\d{4})-(\d{2})", mes)
    if not match:
        return mes
    month = int(match.group(2))
    if not 1 <= month <= 12:
        return mes
    return f"{match.group(1)}-{_MONTHS[month - 1]}"


def ruta_pdf(
    clave: Tuple[str, ...],
    separar_por: Sequence[str],
    run_dir: Optional[Path] = None,
) -> Path:
    """Ruta relativa del PDF de un grupo dentro de la carpeta de la corrida.

    Cada PDF queda suelto en la carpeta de la corrida y su nombre incluye
    exactamente los criterios que lo identifican.
    """
    if not separar_por:
        if run_dir is not None:
            nombre = Path(run_dir).name or "bitacoras"
            return Path(f"{sanitize_filename(nombre)}.pdf")
        return Path("bitacoras.pdf")
    if "avion" in separar_por and "mes" in separar_por:
        valores = dict(zip(separar_por, clave))
        avion, mes = valores["avion"], valores["mes"]
        nombre = "_".join((
            sanitize_filename(avion),
            sanitize_filename(nombre_mes(mes)),
        ))
        return Path(f"{nombre}.pdf")
    if "avion" in separar_por:
        return Path(f"{sanitize_filename(clave[0])}.pdf")
    return Path(f"{sanitize_filename(nombre_mes(clave[0]))}.pdf")


def escribir_pdf_paginas(
    refs: Sequence[PaginaRef],
    output_path: Path,
    dpi: int,
    sources: Optional[PdfDocumentCache] = None,
) -> Path:
    """Escribe un PDF copiando los escaneos originales, sin anotaciones.

    ``dpi`` se conserva en la firma por compatibilidad con el CLI y la GUI,
    pero ya no se usa: exportar debe conservar la página fuente sin
    rasterizarla ni recomprimirla.
    """
    del dpi
    copy_pdf_pages(
        ((ref.pdf_path, ref.page.page_number) for ref in refs),
        output_path,
        sources=sources,
    )
    logger.info(f"[Organize] PDF generado: {output_path} "
                f"({len(refs)} páginas)")
    return output_path


def generar_pdfs(
    reports: Sequence[ValidationReport],
    run_dir: Path,
    separar_por: Sequence[str],
    excluidas: Optional[set[Tuple[str, int]]] = None,
    dpi: int = 150,
) -> List[Path]:
    """Genera los PDFs ordenados (uno por grupo) y devuelve sus rutas.

    Los PDFs de una exportación anterior no se pisan: si el nombre del
    grupo ya existe en la carpeta, la copia nueva lleva sufijo numérico.
    Las rutas se devuelven en el orden de ``sorted(grupos)`` y, al final,
    ``revisar.pdf`` con las bitácoras sin avión confirmado (siempre que
    haya alguna).
    """
    grupos = agrupar_paginas(reports, separar_por, excluidas)
    revisar = paginas_para_revisar(reports)
    rutas: List[Path] = []
    refs = [ref for grupo in grupos.values() for ref in grupo] + revisar
    with PdfDocumentCache(ref.pdf_path for ref in refs) as sources:
        for clave in sorted(grupos):
            ruta = escribir_pdf_paginas(
                grupos[clave],
                unique_path(
                    Path(run_dir) / ruta_pdf(clave, separar_por, run_dir)
                ),
                dpi,
                sources=sources,
            )
            rutas.append(ruta)
        if revisar:
            rutas.append(escribir_pdf_paginas(
                revisar,
                unique_path(Path(run_dir) / NOMBRE_PDF_REVISAR),
                dpi,
                sources=sources,
            ))
    return rutas


# ── PDF único (sin separar en varios archivos) ──────────────────────────

def _preparar_paginas(
    reports: Sequence[ValidationReport],
    excluidas: Optional[set[Tuple[str, int]]],
) -> List[PaginaRef]:
    """Devuelve páginas no en blanco ni excluidas, ordenadas por logpage."""
    excluidas = excluidas or set()
    refs: List[PaginaRef] = []
    for ref in iterar_paginas(reports):
        if ref.page.blank:
            continue
        if (Path(ref.pdf_path).name, ref.page.page_number) in excluidas:
            continue
        if por_revisar(ref.page):
            continue
        refs.append(ref)
    refs.sort(key=lambda ref: clave_orden(ref.page, ref.orden))
    return refs


def _preparar_paginas_incluidas(
    reports: Sequence[ValidationReport],
    incluidas: Optional[set[Tuple[str, int]]],
) -> List[PaginaRef]:
    """Devuelve solo las páginas indicadas, ordenadas globalmente por logpage."""
    incluidas = incluidas or set()
    if not incluidas:
        return []
    refs = [
        ref
        for ref in iterar_paginas(reports)
        if not ref.page.blank
        and not por_revisar(ref.page)
        and (Path(ref.pdf_path).name, ref.page.page_number) in incluidas
    ]
    refs.sort(key=lambda ref: clave_orden(ref.page, ref.orden))
    return refs


def _pagina_divisoria(
    doc: fitz.Document,
    texto: str,
    size: Tuple[float, float] = (842.0, 595.0),
) -> None:
    """Añade una página horizontal blanca con texto grande centrado."""
    ancho, alto = max(size), min(size)
    page = doc.new_page(width=ancho, height=alto)
    page.draw_rect(fitz.Rect(0, 0, ancho, alto), fill=(1, 1, 1), color=None)

    lineas = [linea.strip().upper() for linea in texto.splitlines() if linea.strip()]
    if not lineas:
        return
    fontname = "hebo"  # Helvetica-Bold
    fontsize = 72
    while fontsize >= 12 and max(
        fitz.get_text_length(linea, fontname=fontname, fontsize=fontsize)
        for linea in lineas
    ) > ancho - 100:
        fontsize -= 8
    interlineado = fontsize * 1.25
    primera_base = (
        alto / 2 - (len(lineas) - 1) * interlineado / 2 + fontsize * 0.35
    )
    for indice, linea in enumerate(lineas):
        ancho_texto = fitz.get_text_length(
            linea, fontname=fontname, fontsize=fontsize
        )
        page.insert_text(
            ((ancho - ancho_texto) / 2, primera_base + indice * interlineado),
            linea,
            fontname=fontname,
            fontsize=fontsize,
            color=(0.05, 0.05, 0.05),
        )


def _etiqueta_separador(condicion: str, valor: str) -> str:
    """Texto de la página separadora para un criterio y su valor."""
    if condicion == "mes":
        if valor == "sin_fecha":
            return "SIN FECHA"
        etiqueta = nombre_mes(valor)
        if etiqueta == valor:
            return etiqueta
        anio, _, mes = etiqueta.partition("-")
        return f"{mes} {anio}"
    if condicion == "avion" and valor == "sin_matricula":
        return "SIN MATRICULA"
    return valor


def _etiqueta_grupo(
    clave: Tuple[str, ...],
    criterios: Sequence[str],
) -> str:
    """Une matrícula y mes en el mismo separador cuando se piden ambos."""
    valores = dict(zip(criterios, clave))
    orden = [
        condicion for condicion in ("avion", "mes")
        if condicion in valores
    ]
    return "\n".join(
        _etiqueta_separador(condicion, valores[condicion])
        for condicion in orden
    )


def _claves_en_orden_seccion(
    grupos: Dict[Tuple[str, ...], List[PaginaRef]],
    criterios: Sequence[str],
) -> List[Tuple[str, ...]]:
    """Ordena por matrícula y fecha; valores sin determinar van al final."""

    def section_key(clave: Tuple[str, ...]) -> tuple:
        valores = dict(zip(criterios, clave))
        key: List[tuple] = []
        if "avion" in valores:
            avion = valores["avion"]
            if avion == "sin_matricula":
                key.append((1, ""))
            else:
                match = re.search(r"HP-(\d{4})(CMP|WWP)", avion)
                key.append((
                    0,
                    int(match.group(1)) if match else 1 << 30,
                    match.group(2) if match else avion,
                ))
        if "mes" in valores:
            mes = valores["mes"]
            if mes == "sin_fecha":
                key.append((1, 1 << 30, 13))
            else:
                match = re.fullmatch(r"(\d{4})-(\d{2})", mes)
                key.append((
                    0,
                    int(match.group(1)) if match else 1 << 30,
                    int(match.group(2)) if match else 13,
                ))
        key.append(clave_orden(grupos[clave][0].page, grupos[clave][0].orden))
        return tuple(key)

    return sorted(grupos, key=section_key)


def _tamano_horizontal_fuente(
    sources: PdfDocumentCache,
    ref: PaginaRef,
) -> Tuple[float, float]:
    """Tamaño horizontal de la bitácora que sigue al separador."""
    page = sources.get(ref.pdf_path).load_page(ref.page.page_number - 1)
    return max(page.rect.width, page.rect.height), min(
        page.rect.width, page.rect.height
    )


def _insertar_pagina_fuente(
    doc: fitz.Document,
    sources: PdfDocumentCache,
    ref: PaginaRef,
) -> None:
    """Copia una página fuente al documento sin rasterizarla ni anotarla."""
    doc.insert_pdf(
        sources.get(ref.pdf_path),
        from_page=ref.page.page_number - 1,
        to_page=ref.page.page_number - 1,
    )


def escribir_pdf_unico(
    reports: Sequence[ValidationReport],
    run_dir: Path,
    separar_por: Sequence[str] = (),
    excluidas: Optional[set[Tuple[str, int]]] = None,
    dpi: int = 150,
    discrepancias_al_final: bool = False,
) -> Path:
    """Genera un único PDF con el mismo nombre que ``run_dir``.

    Si ese PDF ya existe (re-export), se conserva y el nuevo se escribe con
    sufijo numérico. Sin páginas que exportar no se escribe nada ni se
    toca el PDF anterior.

    - ``separar_por`` vacío: páginas en orden de logpage, sin separadores.
    - Con ``avion`` y/o ``mes``: las páginas se agrupan por esos criterios
      (ordenadas por logpage dentro del grupo) y cada grupo comienza con una
      página blanca horizontal. Las secciones van por matrícula ascendente y
      fecha cronológica, con los valores sin determinar al final. Si se eligen
      ambos criterios, el mismo separador muestra matrícula y mes.
    - Si ``discrepancias_al_final`` es verdadero, las páginas excluidas de los
      grupos se agregan al final bajo un único separador ``POSIBLES
      DISCREPANCIAS``, ordenadas globalmente por logpage.
    - Las páginas sin avión confirmado cierran el PDF bajo el separador
      ``REVISAR``, se hayan pedido o no las discrepancias y se haya elegido
      o no separar por avión: son las únicas bitácoras que nadie puede
      archivar, así que siempre tienen que quedar juntas y señaladas.
    """
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    base_path = run_dir / f"{run_dir.name}.pdf"

    criterios = list(separar_por or [])
    refs = _preparar_paginas(reports, excluidas) if not criterios else []
    grupos: Dict[Tuple[str, ...], List[PaginaRef]] = {}
    if criterios:
        grupos = agrupar_paginas(reports, criterios, excluidas)
        refs = [ref for grupo in grupos.values() for ref in grupo]
    refs_discrepancias = (
        _preparar_paginas_incluidas(reports, excluidas)
        if discrepancias_al_final
        else []
    )
    refs_revisar = paginas_para_revisar(reports)
    todas_las_refs = refs + refs_discrepancias + refs_revisar

    if not todas_las_refs:
        logger.info(f"[Organize] No hay páginas para exportar: {base_path}")
        return base_path

    output_path = unique_path(base_path)
    doc = fitz.open()
    try:
        with PdfDocumentCache(ref.pdf_path for ref in todas_las_refs) as sources:
            if not criterios:
                for ref in refs:
                    _insertar_pagina_fuente(doc, sources, ref)
            else:
                for clave in _claves_en_orden_seccion(grupos, criterios):
                    grupo = grupos[clave]
                    _pagina_divisoria(
                        doc,
                        _etiqueta_grupo(clave, criterios),
                        _tamano_horizontal_fuente(sources, grupo[0]),
                    )
                    for ref in grupo:
                        _insertar_pagina_fuente(doc, sources, ref)
            if refs_discrepancias:
                _pagina_divisoria(
                    doc,
                    "POSIBLES DISCREPANCIAS",
                    _tamano_horizontal_fuente(sources, refs_discrepancias[0]),
                )
                for ref in refs_discrepancias:
                    _insertar_pagina_fuente(doc, sources, ref)
            if refs_revisar:
                _pagina_divisoria(
                    doc,
                    ETIQUETA_REVISAR,
                    _tamano_horizontal_fuente(sources, refs_revisar[0]),
                )
                for ref in refs_revisar:
                    _insertar_pagina_fuente(doc, sources, ref)
        doc.save(str(output_path), deflate=True)
    finally:
        doc.close()
    logger.info(f"[Organize] PDF único generado: {output_path}")
    return output_path


def escribir_pdf_discrepancias(
    entradas: List[Discrepancia],
    template: Template,
    run_dir: Path,
    dpi: int = 150,
) -> Path:
    """Genera discrepancias.pdf con título y páginas ordenadas por logpage."""
    del template, dpi
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    output_path = unique_path(run_dir / "discrepancias.pdf")
    ordenadas = sorted(
        enumerate(entradas),
        key=lambda par: (
            par[1].log_number
            if par[1].log_number is not None
            else 1 << 30,
            par[0],
        ),
    )
    doc = fitz.open()
    try:
        with PdfDocumentCache(entrada.pdf_path for _, entrada in ordenadas) as sources:
            primera = ordenadas[0][1]
            source_page = sources.get(primera.pdf_path).load_page(
                primera.page_number - 1
            )
            _pagina_divisoria(
                doc,
                "POSIBLES DISCREPANCIAS",
                (source_page.rect.width, source_page.rect.height),
            )
            for _, entrada in ordenadas:
                doc.insert_pdf(
                    sources.get(entrada.pdf_path),
                    from_page=entrada.page_number - 1,
                    to_page=entrada.page_number - 1,
                )
        doc.save(str(output_path), deflate=True)
    finally:
        doc.close()
    logger.info(f"[Discrepancias] PDF generado: {output_path} "
                f"({len(entradas)} páginas)")
    return output_path


# ── PDF de errores OCR (indexación manual) ─────────────────────────────

_OCR_MANUAL_FIELDS = ("matricula", "day", "month", "year", "log_number")


def _campos_sin_resolver(page: PageResult) -> List[FieldResult]:
    """Campos OCR que quedaron sin resolver tras los correctores.

    Un campo se considera pendiente de indexación manual cuando no tiene
    valor legible (None) o su estado tras la corrección sigue siendo ERROR
    (p. ej. una fecha incompleta o una matrícula ilegible que el corrector
    de libros no pudo inferir).
    """
    pendientes: List[FieldResult] = []
    for field in page.fields:
        if field.field_id not in _OCR_MANUAL_FIELDS:
            continue
        if not field.value or field.status is Status.ERROR:
            pendientes.append(field)
    return pendientes


def escribir_pdf_errores(
    reports: Sequence[ValidationReport],
    template: Template,
    run_dir: Path,
    dpi: int = 150,
) -> Path:
    """Genera errores.pdf con las páginas fuente pendientes de indexación.

    Incluye solo las páginas que, después de los correctores por libro,
    tienen campos OCR (matrícula, día/mes/año, log_number) sin valor o en
    estado ERROR. Las páginas se conservan sin anotaciones.

    Returns:
        Ruta del PDF generado (run_dir/errores.pdf).
    """
    del template, dpi
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    output_path = unique_path(run_dir / "errores.pdf")

    pendientes: List[Tuple[PaginaRef, List[FieldResult]]] = []
    for ref in iterar_paginas(reports):
        if ref.page.blank:
            continue
        campos = _campos_sin_resolver(ref.page)
        if campos:
            pendientes.append((ref, campos))

    copy_pdf_pages(
        ((ref.pdf_path, ref.page.page_number) for ref, _ in pendientes),
        output_path,
    )
    logger.info(f"[Errores] PDF generado: {output_path} "
                f"({len(pendientes)} página(s))")
    return output_path


# ── Recortes de firmas (auditoría de bounding boxes) ───────────────────

def escribir_recortes_firmas(
    reports: Sequence[ValidationReport],
    template: Template,
    run_dir: Path,
    dpi: int = 150,
) -> Path:
    """Vuelca los recortes de las regiones de firma como PNG por campo.

    Sirve para auditar a ojo por qué el detector decidió lo que decidió: el
    recorte incluye el mismo margen que usa ``detect_signature`` y el nombre
    del archivo lleva el veredicto (``true``/``false``/``unclear``), de modo
    que basta ordenar la carpeta por nombre para revisar juntos todos los
    casos inciertos. La página se renderiza sin alineación, por lo que el
    encuadre puede variar unos píxeles respecto al recorte real.
    """
    run_dir = Path(run_dir)
    out_root = run_dir / "recortes_firmas"
    campos = [f for f in template.fields if f.type is FieldType.SIGNATURE]
    if not campos:
        logger.warning("[Recortes] La plantilla no define campos de firma")
        return out_root
    for campo in campos:
        (out_root / campo.id).mkdir(parents=True, exist_ok=True)

    total = 0
    for ref in iterar_paginas(reports):
        if ref.page.blank:
            continue
        try:
            image_bgr = render_page(Path(ref.pdf_path), ref.page.page_number, dpi)
            imagen = Image.fromarray(
                np.ascontiguousarray(image_bgr[:, :, ::-1])
            ).convert("RGB")
        except Exception as exc:  # noqa: BLE001 - página no renderizable
            logger.warning(f"[Recortes] Página {ref.page.page_number} "
                           f"no renderizable: {exc}")
            continue
        base = f"{Path(ref.pdf_path).stem}_p{ref.page.page_number:03d}.png"
        leidos = {f.field_id: f.value for f in ref.page.fields}
        for campo in campos:
            left, top, right, bottom = campo.rect_pixels(
                imagen.width, imagen.height
            )
            margen_x = max(1, round(SIGNATURE_PAD_X * (right - left)))
            margen_y = max(1, round(SIGNATURE_PAD_Y * (bottom - top)))
            recorte = np.array(imagen)[
                max(0, top - margen_y):min(imagen.height, bottom + margen_y),
                max(0, left - margen_x):min(imagen.width, right + margen_x),
            ]
            veredicto = leidos.get(campo.id) or "sin_lectura"
            Image.fromarray(recorte).save(
                out_root / campo.id / f"{veredicto}_{base}"
            )
            total += 1
    logger.info(f"[Recortes] {total} recorte(s) guardados en {out_root}")
    return out_root

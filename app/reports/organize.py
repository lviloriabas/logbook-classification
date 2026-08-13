"""Generación de PDFs de bitácoras ordenados y separados.

- **PDFs ordenados**: reordenan los escaneos tal cual (sin encabezados ni
  anotaciones, listos para subir a la plataforma de indexado), agrupados
  por avión (matrícula corregida) y/o mes (``page.date`` -> YYYY-MM), con
  las páginas en orden de libro (``log_number``) y logpage.
- **PDF de discrepancias**: las páginas con firmas faltantes o inciertas,
  sin anotaciones, ordenadas por avión y logpage.
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
- ``HP-XXXXCMP/2026-JUL.pdf``        por avión y mes: una carpeta por
                                     matrícula y, dentro, un PDF por mes
                                     (``sf.pdf`` para las páginas sin fecha)
- ``discrepancias.pdf``              páginas con discrepancias

Las páginas sin matrícula legible van al grupo ``sin_matricula`` y las
sin fecha legible al grupo ``sin_fecha`` (archivo ``sf.pdf``), de modo
que ninguna bitácora queda por fuera de los PDFs generados.
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
from app.utils.io import sanitize_filename
from app.validation.discrepancias import (
    Discrepancia,
)
from app.validation.grouping import log_number
from app.vision.pdf_loader import PdfDocumentCache, copy_pdf_pages, render_page

_DATE_RE = re.compile(r"^\d{4}/\d{2}/\d{2}$")
_MATRICULA_RE = re.compile(r"^HP-\d{4}(CMP|WWP)$")
_MONTHS = (
    "JAN", "FEB", "MAR", "APR", "MAY", "JUN",
    "JUL", "AUG", "SEP", "OCT", "NOV", "DEC",
)

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
    return "sin_matricula"


def clave_mes(page: PageResult) -> str:
    """Mes de la página (YYYY-MM), o 'sin_fecha' si no es legible."""
    if page.date and _DATE_RE.match(page.date):
        return page.date[:7].replace("/", "-")
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
            PDF de discrepancias). Páginas en blanco siempre se excluyen.

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

    Las divisiones por matrícula son carpetas solo cuando también se
    separa por mes; en cualquier otro caso las divisiones son PDFs
    sueltos en la carpeta madre (ver convenciones del módulo).
    """
    if not separar_por:
        if run_dir is not None:
            nombre = Path(run_dir).name or "bitacoras"
            return Path(f"{sanitize_filename(nombre)}.pdf")
        return Path("bitacoras.pdf")
    if "avion" in separar_por and "mes" in separar_por:
        avion, mes = clave
        return (Path(sanitize_filename(avion))
                / f"{sanitize_filename(nombre_mes(mes))}.pdf")
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
    """Genera los PDFs ordenados (uno por grupo) y devuelve sus rutas."""
    grupos = agrupar_paginas(reports, separar_por, excluidas)
    rutas: List[Path] = []
    refs = [ref for grupo in grupos.values() for ref in grupo]
    with PdfDocumentCache(ref.pdf_path for ref in refs) as sources:
        for clave in sorted(grupos):
            ruta = escribir_pdf_paginas(
                grupos[clave],
                Path(run_dir) / ruta_pdf(clave, separar_por, run_dir),
                dpi,
                sources=sources,
            )
            rutas.append(ruta)
    return rutas


# ── PDF único (sin separar en varios archivos) ──────────────────────────

def _preparar_paginas(
    reports: Sequence[ValidationReport],
    excluidas: Optional[set[Tuple[str, int]]],
) -> List[PaginaRef]:
    """Devuelve las páginas no en blanco ni excluidas, en orden original."""
    excluidas = excluidas or set()
    refs: List[PaginaRef] = []
    for ref in iterar_paginas(reports):
        if ref.page.blank:
            continue
        if (Path(ref.pdf_path).name, ref.page.page_number) in excluidas:
            continue
        refs.append(ref)
    return refs


def _pagina_divisoria(doc: fitz.Document, texto: str) -> None:
    """Página blanca A4 con el texto (matrícula o mes) en grande, centrado."""
    ancho, alto = 595.0, 842.0
    page = doc.new_page(width=ancho, height=alto)
    page.draw_rect(fitz.Rect(0, 0, ancho, alto), fill=(1, 1, 1), color=None)

    texto = texto.upper()
    fontname = "hebo"  # Helvetica-Bold
    fontsize = 72
    while fontsize >= 12 and fitz.get_text_length(
            texto, fontname=fontname, fontsize=fontsize) > ancho - 100:
        fontsize -= 8
    ancho_texto = fitz.get_text_length(
        texto, fontname=fontname, fontsize=fontsize
    )
    page.insert_text(
        ((ancho - ancho_texto) / 2, alto / 2 + fontsize * 0.35),
        texto,
        fontname=fontname,
        fontsize=fontsize,
        color=(0.05, 0.05, 0.05),
    )


def _etiqueta_separador(condicion: str, valor: str) -> str:
    """Texto de la página separadora para un criterio y su valor."""
    if condicion == "mes" and valor != "sin_fecha":
        return nombre_mes(valor).replace("-", "/")
    return valor


def escribir_pdf_unico(
    reports: Sequence[ValidationReport],
    run_dir: Path,
    separar_por: Sequence[str] = (),
    excluidas: Optional[set[Tuple[str, int]]] = None,
    dpi: int = 150,
) -> Path:
    """Genera un único PDF con el mismo nombre que ``run_dir``.

    - ``separar_por`` vacío: páginas en orden original, sin separadores.
    - Con ``avion`` y/o ``mes``: las páginas se agrupan por esos criterios
      (ordenadas por logpage dentro del grupo) y se inserta una página en
      blanco con la matrícula o el mes en grande cada vez que cambia el
      grupo, sirviendo como separador visual.
    """
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    output_path = run_dir / f"{run_dir.name}.pdf"

    criterios = list(separar_por or [])
    refs = _preparar_paginas(reports, excluidas) if not criterios else []
    grupos: Dict[Tuple[str, ...], List[PaginaRef]] = {}
    if criterios:
        grupos = agrupar_paginas(reports, criterios, excluidas)
        refs = [ref for grupo in grupos.values() for ref in grupo]

    if not refs:
        output_path.unlink(missing_ok=True)
        logger.info(f"[Organize] No hay páginas para exportar: {output_path}")
        return output_path

    doc = fitz.open()
    try:
        with PdfDocumentCache(ref.pdf_path for ref in refs) as sources:
            if not criterios:
                for ref in refs:
                    doc.insert_pdf(
                        sources.get(ref.pdf_path),
                        from_page=ref.page.page_number - 1,
                        to_page=ref.page.page_number - 1,
                    )
            else:
                clave_previa: Optional[Tuple[str, ...]] = None
                for clave in sorted(grupos):
                    if clave_previa is not None:
                        for i, condicion in enumerate(criterios):
                            if clave[i] != clave_previa[i]:
                                _pagina_divisoria(
                                    doc,
                                    _etiqueta_separador(condicion, clave[i]),
                                )
                                break
                    clave_previa = clave
                    for ref in grupos[clave]:
                        doc.insert_pdf(
                            sources.get(ref.pdf_path),
                            from_page=ref.page.page_number - 1,
                            to_page=ref.page.page_number - 1,
                        )
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
    """Genera discrepancias.pdf con las páginas fuente sin anotaciones.

    El orden ya viene aplicado por ``clasificar_lote``. ``template`` y
    ``dpi`` se conservan en la firma para compatibilidad, pero la salida no
    dibuja información sobre las bitácoras.
    """
    del template, dpi
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    output_path = run_dir / "discrepancias.pdf"
    copy_pdf_pages(
        ((entrada.pdf_path, entrada.page_number) for entrada in entradas),
        output_path,
    )
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
    output_path = run_dir / "errores.pdf"

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

    Útil para verificar visualmente que cada bounding box capture solo la
    línea de firma (sin etiquetas impresas ni contenido vecino). El
    recorte se toma sobre la página renderizada sin alineación, por lo que
    puede variar unos píxeles respecto al recorte real del pipeline.
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
        nombre = f"{Path(ref.pdf_path).stem}_p{ref.page.page_number:03d}.png"
        for campo in campos:
            left, top, right, bottom = campo.rect_pixels(
                imagen.width, imagen.height
            )
            recorte = np.array(imagen)[top:bottom, left:right]
            Image.fromarray(recorte).save(out_root / campo.id / nombre)
            total += 1
    logger.info(f"[Recortes] {total} recorte(s) guardados en {out_root}")
    return out_root

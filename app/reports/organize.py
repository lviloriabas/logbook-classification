"""Generación de PDFs de bitácoras ordenados y PDF de discrepancias.

- **PDFs ordenados**: reordenan los escaneos tal cual (sin encabezados ni
  anotaciones, listos para subir a la plataforma de indexado), agrupados
  por avión (matrícula corregida) y/o mes (``page.date`` -> YYYY-MM), con
  las páginas en orden de libro (``log_number``) y logpage.
- **PDF de discrepancias**: las páginas con firmas faltantes o inciertas,
  anotadas con las razones, ordenadas por avión y logpage.
- **Recortes de firmas**: volcado de las regiones de firma para auditar
  visualmente los bounding boxes.

Convenciones de nombres (rutas relativas a la carpeta de la corrida):

- ``<carpeta>.pdf``                  un único PDF: usa el mismo nombre que la
                                     carpeta de la corrida, sin separadores si
                                     no se eligen condiciones, o con páginas
                                     en blanco de matrícula/mes en grande como
                                     separadores entre grupos
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

import io
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import fitz  # PyMuPDF
import numpy as np
from loguru import logger
from PIL import Image, ImageDraw, ImageFont

from app.models.schemas import (
    FieldResult,
    PageResult,
    Status,
    ValidationReport,
)
from app.templates.schema import FieldType, Template
from app.utils.io import sanitize_filename
from app.validation.discrepancias import (
    Categoria,
    Discrepancia,
)
from app.validation.grouping import log_number
from app.vision.pdf_loader import render_page

_DATE_RE = re.compile(r"^\d{4}/\d{2}/\d{2}$")
_MATRICULA_RE = re.compile(r"^HP-\d{4}(CMP|WWP)$")
_MONTHS = (
    "JAN", "FEB", "MAR", "APR", "MAY", "JUN",
    "JUL", "AUG", "SEP", "OCT", "NOV", "DEC",
)

COLOR_MISSING = (207, 34, 46)     # rojo
COLOR_UNCERTAIN = (154, 103, 0)   # naranja
COLOR_HEADER_BG = (30, 40, 60)
COLOR_HEADER_FG = (255, 255, 255)


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


def _load_font(size: int) -> ImageFont.ImageFont:
    """Fuente TrueType de Pillow con fallback a la fuente por defecto."""
    for name in ("arial.ttf", "DejaVuSans.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _render_pagina(pdf_path: str, page_number: int, dpi: int) -> Image.Image:
    """Renderiza una página del escaneo como imagen RGB de Pillow."""
    image_bgr = render_page(Path(pdf_path), page_number, dpi)
    return Image.fromarray(
        np.ascontiguousarray(image_bgr[:, :, ::-1])
    ).convert("RGB")


def _insertar_pagina(doc: fitz.Document, imagen: Image.Image,
                     dpi: int) -> None:
    """Inserta una imagen completa como página del PDF.

    Se codifica como JPEG (calidad alta) en vez de PNG: el stream dentro
    del documento pesa ~5-8 veces menos, reduciendo la memoria y el tiempo
    de generación de PDFs con cientos de páginas.
    """
    buffer = io.BytesIO()
    imagen.save(buffer, format="JPEG", quality=90, optimize=True)
    scale = dpi / 72.0
    page = doc.new_page(
        width=round(imagen.width * scale),
        height=round(imagen.height * scale),
    )
    page.insert_image(
        fitz.Rect(0, 0, round(imagen.width * scale),
                  round(imagen.height * scale)),
        stream=buffer.getvalue(),
    )


def escribir_pdf_paginas(refs: Sequence[PaginaRef], output_path: Path,
                         dpi: int) -> Path:
    """Escribe un PDF con los escaneos tal cual (sin anotaciones)."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    for ref in refs:
        imagen = _render_pagina(ref.pdf_path, ref.page.page_number, dpi)
        _insertar_pagina(doc, imagen, dpi)
    doc.save(str(output_path), deflate=True)
    doc.close()
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
    for clave in sorted(grupos):
        ruta = escribir_pdf_paginas(
            grupos[clave],
            Path(run_dir) / ruta_pdf(clave, separar_por, run_dir),
            dpi,
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

    doc = fitz.open()
    criterios = list(separar_por or [])

    def _escribir(refs: Sequence[PaginaRef]) -> None:
        for ref in refs:
            imagen = _render_pagina(ref.pdf_path, ref.page.page_number, dpi)
            _insertar_pagina(doc, imagen, dpi)

    if not criterios:
        _escribir(_preparar_paginas(reports, excluidas))
    else:
        grupos = agrupar_paginas(reports, criterios, excluidas)
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
            _escribir(grupos[clave])

    doc.save(str(output_path), deflate=True)
    doc.close()
    logger.info(f"[Organize] PDF único generado: {output_path}")
    return output_path


# ── PDF de discrepancias ───────────────────────────────────────────────

def _dibujar_banda(image: Image.Image, texto: str, razones: List[str]
                   ) -> Image.Image:
    """Banda superior con identificación de la página y sus razones."""
    draw = ImageDraw.Draw(image)
    font_titulo = _load_font(max(16, image.height // 90))
    font_razon = _load_font(max(13, image.height // 110))
    linea1 = draw.textbbox((0, 0), texto, font=font_titulo)
    alto_titulo = linea1[3] - linea1[1] + 10
    lineas = razones or ["Sin razones detalladas"]
    alto_razones = sum(
        draw.textbbox((0, 0), linea, font=font_razon)[3]
        - draw.textbbox((0, 0), linea, font=font_razon)[1]
        + 4
        for linea in lineas
    )
    alto_total = alto_titulo + alto_razones + 14
    draw.rectangle((0, 0, image.width, alto_total), fill=COLOR_HEADER_BG)
    draw.text((10, 6), texto, fill=COLOR_HEADER_FG, font=font_titulo)
    y = 6 + alto_titulo
    for linea in lineas:
        draw.text((14, y), "• " + linea, fill=(255, 210, 120), font=font_razon)
        y += draw.textbbox((0, 0), linea, font=font_razon)[3] \
            - draw.textbbox((0, 0), linea, font=font_razon)[1] + 4
    draw.rectangle((0, alto_total, image.width, alto_total + 3),
                   fill=COLOR_HEADER_BG)
    return image


def _marcar_campo(image: Image.Image, field_template, color: Tuple[int, ...]
                  ) -> Image.Image:
    """Dibuja un rectángulo translúcido sobre la región del campo."""
    width, height = image.size
    left, top, right, bottom = field_template.rect_pixels(width, height)
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.rectangle((left, top, right, bottom), fill=color + (60,),
                   outline=color + (255,), width=4)
    return Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")


def _pagina_resumen(entradas: List[Discrepancia]) -> Image.Image:
    """Página inicial con resumen por avión y leyenda de colores."""
    width, height = 1240, 1600
    imagen = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(imagen)
    font_titulo = _load_font(30)
    font_parrafo = _load_font(20)

    draw.text((40, 30), "Páginas con discrepancias de firma",
              fill=(20, 20, 20), font=font_titulo)
    draw.text((40, 80),
              "Las páginas listadas no se incluyen en los PDFs por avión "
              "y deben corregirse o revisarse.",
              fill=(60, 60, 60), font=font_parrafo)

    draw.rectangle((40, 130, 130, 160), fill=COLOR_MISSING + (255,))
    draw.text((150, 130), "Firma faltante (confirmada)",
              fill=(20, 20, 20), font=font_parrafo)
    draw.rectangle((40, 170, 130, 200), fill=COLOR_UNCERTAIN + (255,))
    draw.text((150, 170), "Firma incierta (revisar)",
              fill=(20, 20, 20), font=font_parrafo)

    y = 240
    por_avion: Dict[str, List[Discrepancia]] = {}
    for entrada in entradas:
        por_avion.setdefault(entrada.matricula or "sin matrícula",
                             []).append(entrada)

    draw.text((40, y), f"Resumen ({len(entradas)} página(s), "
              f"{len(por_avion)} avión(es)):",
              fill=(20, 20, 20), font=font_parrafo)
    y += 36
    for avion in sorted(por_avion):
        grupo = por_avion[avion]
        faltantes = sum(1 for e in grupo if e.categoria is Categoria.MISSING)
        inciertas = sum(1 for e in grupo if e.categoria is Categoria.UNCERTAIN)
        draw.text((60, y),
                  f"{avion}: {len(grupo)} página(s) "
                  f"({faltantes} faltante(s), {inciertas} para revisar)",
                  fill=(20, 20, 20), font=font_parrafo)
        y += 30
        if y > height - 60:
            break
    return imagen


def escribir_pdf_discrepancias(
    entradas: List[Discrepancia],
    template: Template,
    run_dir: Path,
    dpi: int = 150,
) -> Path:
    """Genera discrepancias.pdf con las páginas afectadas anotadas.

    Orden (ya aplicado por clasificar_lote): avión, luego log_number.
    """
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    output_path = run_dir / "discrepancias.pdf"
    doc = fitz.open()

    # Siempre con página de resumen (aunque no haya entradas), para no
    # guardar un PDF vacío.
    _insertar_pagina(doc, _pagina_resumen(entradas), dpi=150)

    for entrada in entradas:
        imagen = _render_pagina(entrada.pdf_path, entrada.page_number, dpi)
        titulo = (
            f"{entrada.matricula or 'SIN MATRÍCULA'} | "
            f"Log {entrada.log_number if entrada.log_number is not None else '???'} | "
            f"{entrada.tipo.value.upper()} | "
            f"{entrada.categoria.value.upper()}"
        )
        imagen = _dibujar_banda(imagen, titulo, entrada.razones())
        for campo in entrada.campos:
            field_template = template.field(campo.field_id)
            if field_template is None:
                continue
            color = (COLOR_MISSING if campo.categoria is Categoria.MISSING
                     else COLOR_UNCERTAIN)
            imagen = _marcar_campo(imagen, field_template, color)
        _insertar_pagina(doc, imagen, dpi)

    doc.save(str(output_path), deflate=True)
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


def _pagina_resumen_errores(
    pendientes: Sequence[Tuple[PaginaRef, List[FieldResult]]],
) -> Image.Image:
    """Página inicial con el resumen de páginas pendientes de indexar."""
    width, height = 1240, 1600
    imagen = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(imagen)
    font_titulo = _load_font(30)
    font_parrafo = _load_font(20)

    draw.text((40, 30), "Páginas con campos sin resolver",
              fill=(20, 20, 20), font=font_titulo)
    draw.text((40, 80),
              "El OCR no pudo leer (o los correctores no pudieron inferir) "
              "los campos marcados. Estas páginas deben indexarse "
              "manualmente.",
              fill=(60, 60, 60), font=font_parrafo)

    draw.rectangle((40, 140, 130, 170), fill=COLOR_MISSING + (255,))
    draw.text((150, 140), "Campo sin valor legible / error tras corrección",
              fill=(20, 20, 20), font=font_parrafo)

    y = 220
    draw.text((40, y), f"Resumen ({len(pendientes)} página(s)):",
              fill=(20, 20, 20), font=font_parrafo)
    y += 36
    for ref, campos in pendientes:
        nombres = ", ".join(c.field_id for c in campos)
        numero = log_number(ref.page)
        draw.text(
            (60, y),
            f"P{ref.page.page_number:02d} [{Path(ref.pdf_path).stem}] "
            f"log={numero if numero is not None else '???'} -> {nombres}",
            fill=(20, 20, 20), font=font_parrafo,
        )
        y += 30
        if y > height - 60:
            break
    return imagen


def escribir_pdf_errores(
    reports: Sequence[ValidationReport],
    template: Template,
    run_dir: Path,
    dpi: int = 150,
) -> Path:
    """Genera errores.pdf con las páginas pendientes de indexación manual.

    Incluye solo las páginas que, después de los correctores por libro,
    tienen campos OCR (matrícula, día/mes/año, log_number) sin valor o en
    estado ERROR. Cada página se anota con el nombre de la bitácora y los
    campos pendientes, y se marcan los recuadros correspondientes.

    Returns:
        Ruta del PDF generado (run_dir/errores.pdf).
    """
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

    doc = fitz.open()
    # Siempre con página de resumen (aunque no haya pendientes), para no
    # guardar un PDF vacío.
    _insertar_pagina(doc, _pagina_resumen_errores(pendientes), dpi=150)

    for ref, campos in pendientes:
        imagen = _render_pagina(ref.pdf_path, ref.page.page_number, dpi)
        numero = log_number(ref.page)
        razones = [
            f"{c.field_id}: "
            + (f"sin valor" if not c.value else f"{c.value!r} -> {c.status.value}")
            + (f" ({c.comment})" if c.comment else "")
            for c in campos
        ]
        titulo = (
            f"{Path(ref.pdf_path).stem} | Página {ref.page.page_number:02d} | "
            f"Log {numero if numero is not None else '???'} | "
            f"{clave_avion(ref.page)}"
        )
        imagen = _dibujar_banda(imagen, titulo, razones)
        for campo in campos:
            field_template = template.field(campo.field_id)
            if field_template is None:
                continue
            imagen = _marcar_campo(imagen, field_template, COLOR_MISSING)
        _insertar_pagina(doc, imagen, dpi)

    doc.save(str(output_path), deflate=True)
    doc.close()
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
            imagen = _render_pagina(ref.pdf_path, ref.page.page_number, dpi)
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

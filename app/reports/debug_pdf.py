"""PDF de debug: bounding boxes de los campos sobre las bitácoras.

Genera un PDF combinado (una página por página procesada) en el que se
dibujan los rectángulos de la plantilla sobre la imagen escaneada,
coloreados por el estado de cada campo (OK / WARNING / ERROR) y etiquetados
con el id y el valor extraído, para verificar visualmente dónde recorta
el pipeline cada región.
"""

from __future__ import annotations

from pathlib import Path
from typing import List

import pymupdf as fitz  # PyMuPDF
import numpy as np
from loguru import logger
from PIL import Image, ImageDraw, ImageFont

from app.models.schemas import Status, ValidationReport
from app.templates.schema import Template
from app.vision.pdf_loader import render_page

# Colores por estado (idénticos a los de la GUI)
STATUS_COLORS = {
    Status.OK: (26, 127, 55),      # verde
    Status.WARNING: (154, 103, 0), # naranja
    Status.ERROR: (207, 34, 46),   # rojo
    "MISSING": (120, 120, 120),    # gris (sin resultado)
}

_LABEL_BG = (245, 245, 245, 220)
# Opacidad del relleno de los bounding boxes (0-255): translúcido para
# poder ver el contenido de la bitácora detrás del rectángulo.
_BOX_FILL_ALPHA = 65


def _load_font(size: int) -> ImageFont.ImageFont:
    """Fuente TrueType de Pillow con fallback a la fuente por defecto."""
    candidates = [
        "arial.ttf",
        "DejaVuSans.ttf",
        "DejaVuSans-Bold.ttf",
    ]
    for name in candidates:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _status_color(page, field_id: str) -> tuple:
    """Color del rectángulo según el estado del campo en la página."""
    for field in page.fields:
        if field.field_id == field_id:
            return STATUS_COLORS[field.status]
    return STATUS_COLORS["MISSING"]


def _field_label(page, field_id: str) -> str:
    """Etiqueta del campo: id + valor (+ confianza en campos OCR)."""
    for field in page.fields:
        if field.field_id == field_id:
            value = field.value or ""
            if field.field_type in ("ocr", "text", "date") and value:
                return f"{field_id}:{value} ({field.confidence:.2f})"
            return f"{field_id}:{value}"
    return field_id


def _draw_box(
    draw: ImageDraw.ImageDraw,
    left: int, top: int, right: int, bottom: int,
    color: tuple,
) -> None:
    """Dibuja un rectángulo con borde opaco y relleno translúcido."""
    draw.rectangle((left, top, right, bottom),
                   fill=color + (_BOX_FILL_ALPHA,),
                   outline=color + (255,), width=4)


def _draw_label(
    draw: ImageDraw.ImageDraw,
    image: Image.Image,
    text: str,
    left: int, top: int,
    font: ImageFont.ImageFont,
) -> None:
    """Etiqueta con fondo sólido sobre la esquina superior-izquierda del box."""
    text_box = draw.textbbox((0, 0), text, font=font)
    label_w = text_box[2] - text_box[0] + 8
    label_h = text_box[3] - text_box[1] + 6
    label_y = max(0, top - label_h - 2)
    draw.rectangle(
        (left, label_y, min(left + label_w, image.width - 1), label_y + label_h),
        fill=_LABEL_BG,
    )
    draw.text((left + 4, label_y + 2), text, fill=(0, 0, 0), font=font)


def _legend_image(width: int) -> Image.Image:
    """Imagen de leyenda con el significado de los colores."""
    legend = Image.new("RGB", (width, 90), (255, 255, 255))
    overlay = Image.new("RGBA", legend.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font = _load_font(16)
    items = [
        ("OK", STATUS_COLORS[Status.OK]),
        ("WARNING", STATUS_COLORS[Status.WARNING]),
        ("ERROR", STATUS_COLORS[Status.ERROR]),
        ("SIN RESULTADO", STATUS_COLORS["MISSING"]),
    ]
    x = 20
    for text, color in items:
        draw.rectangle((x, 20, x + 30, 50),
                       fill=color + (_BOX_FILL_ALPHA,),
                       outline=color + (255,), width=3)
        draw.text((x + 38, 26), text, fill=(0, 0, 0), font=font)
        x += 40 + draw.textbbox((0, 0), text, font=font)[2] + 40
    return Image.alpha_composite(legend.convert("RGBA"),
                                 overlay).convert("RGB")


def write_debug_pdf(
    reports: List[ValidationReport],
    template: Template,
    output_path: Path,
    dpi: int = 150,
    legend: bool = False,
    crop_padding: float = 0.01,
) -> Path:
    """Genera el PDF combinado con los bounding boxes de los campos.

    Args:
        reports: Reportes de validación (uno por bitácora procesada).
        template: Plantilla que define las regiones de los campos.
        output_path: Ruta del PDF de salida.
        dpi: Resolución del renderizado de cada página.
        legend: Incluir página inicial con la leyenda de colores.
            (deshabilitado por defecto)
        crop_padding: Margen relativo que el pipeline añade a cada
            recuadro al recortar la región de lectura.

    Returns:
        La ruta del archivo generado.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    doc = fitz.open()
    total_pages = sum(len(r.pages) for r in reports)

    if legend and reports:
        page = doc.new_page(width=595, height=90)
        legend_img = _legend_image(595)
        _insert_image(page, legend_img, scale=1.0)

    for report in reports:
        book_name = Path(report.pdf_path).name
        logger.info(f"[DebugPDF] {book_name}: dibujando bounding boxes")
        src = fitz.open(str(report.pdf_path))
        try:
            for page_result in report.pages:
                src_rect = src.load_page(page_result.page_number - 1).rect
                image_bgr = render_page(Path(report.pdf_path),
                                        page_result.page_number, dpi)
                image_rgb = Image.fromarray(
                    np.ascontiguousarray(image_bgr[:, :, ::-1])
                ).convert("RGB")

                # Overlay RGBA: los rectángulos translúcidos se componen
                # sobre la página original sin tapar el contenido escaneado.
                base = image_rgb.convert("RGBA")
                overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
                draw = ImageDraw.Draw(overlay)
                font = _load_font(max(12, image_rgb.height // 80))

                if page_result.blank:
                    draw.text(
                        (20, overlay.height // 2),
                        "PÁGINA EN BLANCO",
                        fill=(120, 120, 120),
                        font=_load_font(overlay.height // 20),
                    )
                else:
                    width, height = overlay.size
                    for field in template.fields:
                        left, top, right, bottom = field.rect_pixels(
                            width, height
                        )
                        # Recuadro exacto que se recorta para la lectura
                        # (mismo margen relativo al campo que crop_region).
                        px = max(1, round(crop_padding * (right - left)))
                        py = max(1, round(crop_padding * (bottom - top)))
                        left = max(0, left - px)
                        top = max(0, top - py)
                        right = min(width - 1, right + px)
                        bottom = min(height - 1, bottom + py)
                        color = _status_color(page_result, field.id)
                        _draw_box(draw, left, top, right, bottom, color)
                        _draw_label(draw, overlay,
                                    _field_label(page_result, field.id),
                                    left, top, font)

                image = Image.alpha_composite(base, overlay).convert("RGB")
                # Página de salida del mismo tamaño que la bitácora
                # original (puntos del PDF fuente); la imagen se inserta
                # a escala 72/dpi para que encaje exactamente.
                fitz_page = doc.new_page(
                    width=src_rect.width, height=src_rect.height
                )
                _insert_image(fitz_page, image, scale=72.0 / dpi)
        finally:
            src.close()

    doc.save(str(output_path), deflate=True)
    doc.close()
    logger.info(f"[DebugPDF] Generado: {output_path} "
                f"({total_pages} páginas de bitácoras)")
    return output_path


def _insert_image(page: fitz.Page, image: Image.Image, scale: float = 1.0) -> None:
    """Inserta una imagen PIL como fondo completo de la página PDF.

    Se codifica como JPEG (calidad alta) en vez de PNG para reducir la
    memoria y el tiempo con corridas de cientos de páginas.
    """
    import io

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=90, optimize=True)
    width = round(image.width * scale)
    height = round(image.height * scale)
    page.insert_image(
        fitz.Rect(0, 0, width, height), stream=buffer.getvalue()
    )

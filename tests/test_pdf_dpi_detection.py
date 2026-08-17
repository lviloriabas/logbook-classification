"""El DPI del escaneo se lee de los metadatos, no descomprimiendo la página.

``detect_dpi`` solo necesita el ancho en píxeles de la imagen incrustada, que
``get_images(full=True)`` ya devuelve. Extraerla con ``extract_image``
obligaba a descomprimir el escaneo completo: medio segundo por PDF que la
ventana principal pagaba en el arranque por cada archivo de ``input/``.
"""

from __future__ import annotations

import numpy as np
import pymupdf as fitz

from app.vision.pdf_loader import PdfPageRenderer


def _scanned_pdf(path, width_px: int, width_pts: float) -> None:
    """PDF de una página con una imagen incrustada de ancho conocido."""
    height_px = int(width_px * 1.3)
    pixels = np.full((height_px, width_px, 3), 255, dtype=np.uint8)
    pixmap = fitz.Pixmap(
        fitz.csRGB, width_px, height_px, pixels.tobytes(), False
    )
    document = fitz.open()
    page = document.new_page(width=width_pts, height=width_pts * 1.3)
    page.insert_image(page.rect, pixmap=pixmap)
    document.save(path)
    document.close()


def test_dpi_comes_from_the_embedded_image_width(tmp_path):
    # 1700 px sobre 612 pt (carta) ≈ 200 DPI.
    pdf = tmp_path / "scan.pdf"
    _scanned_pdf(pdf, width_px=1700, width_pts=612.0)

    with PdfPageRenderer(pdf) as renderer:
        assert renderer.detect_dpi(default=999) == 200


def test_page_is_never_decompressed_to_read_the_dpi(tmp_path):
    """La ruta cara (``extract_image``) no puede volver a aparecer."""
    pdf = tmp_path / "scan.pdf"
    _scanned_pdf(pdf, width_px=1700, width_pts=612.0)

    with PdfPageRenderer(pdf) as renderer:
        extractions = []
        original = renderer.document.extract_image

        def spy(xref, *args, **kwargs):
            extractions.append(xref)
            return original(xref, *args, **kwargs)

        renderer.document.extract_image = spy
        renderer.detect_dpi(default=999)

        assert extractions == []


def test_a_pdf_without_images_keeps_the_default(tmp_path):
    pdf = tmp_path / "vector.pdf"
    document = fitz.open()
    document.new_page(width=612.0, height=792.0)
    document.save(pdf)
    document.close()

    with PdfPageRenderer(pdf) as renderer:
        assert renderer.detect_dpi(default=300) == 300

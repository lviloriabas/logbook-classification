"""Carga de PDFs escaneados a imágenes (PyMuPDF)."""

from __future__ import annotations

from pathlib import Path
from typing import List

import pymupdf as fitz  # PyMuPDF
from loguru import logger


def render_pdf_pages(pdf_path: Path, dpi: int = 200) -> List[np.ndarray]:
    """Renderiza cada página del PDF a una imagen numpy (formato BGR).

    Args:
        pdf_path: Ruta del PDF escaneado.
        dpi: Resolución del renderizado.

    Returns:
        Lista de imágenes BGR, una por página, en orden.
    """
    import cv2
    import numpy as np

    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF no encontrado: {pdf_path}")

    images: List[np.ndarray] = []
    zoom = dpi / 72.0  # 72 puntos por pulgada en PDF
    doc = fitz.open(str(pdf_path))
    try:
        for page_num in range(len(doc)):
            page = doc.load_page(page_num)
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
            arr = np.frombuffer(pix.samples, dtype=np.uint8)
            arr = arr.reshape(pix.height, pix.width, pix.n)

            if pix.n == 1:
                arr = cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
            elif pix.n == 4:
                arr = cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
            else:
                arr = arr[:, :, ::-1]  # RGB → BGR

            images.append(np.ascontiguousarray(arr))
            logger.debug(f"Página {page_num + 1} renderizada: "
                         f"{arr.shape[1]}x{arr.shape[0]}")
    finally:
        doc.close()

    logger.info(f"{len(images)} páginas renderizadas a {dpi} DPI")
    return images


def detect_dpi(pdf_path: Path, default: int = 200) -> int:
    """Detecta la resolución efectiva de un PDF escaneado.

    Calcula el DPI a partir de la primera imagen incrustada de la página 1:
    ``píxeles de la imagen / (ancho de página en pulgadas)``. Si el PDF no
    tiene imagen (p. ej. vectorial) devuelve :param:`default`.
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF no encontrado: {pdf_path}")
    doc = fitz.open(str(pdf_path))
    try:
        if not len(doc):
            return default
        page = doc.load_page(0)
        width_pts = max(page.mediabox.width, 1.0)
        for img in page.get_images(full=True):
            xref = img[0]
            pix = doc.extract_image(xref)
            dpi = round(pix.get("width", 0) * 72.0 / width_pts)
            if 72 <= dpi <= 600:
                return dpi
        return default
    finally:
        doc.close()


def page_count(pdf_path: Path) -> int:
    """Número de páginas del PDF."""
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF no encontrado: {pdf_path}")
    doc = fitz.open(str(pdf_path))
    try:
        return len(doc)
    finally:
        doc.close()


def render_page(pdf_path: Path, page_number: int,
                dpi: int = 150) -> np.ndarray:
    """Renderiza una sola página del PDF (para vistas previas)."""
    import cv2
    import numpy as np

    zoom = dpi / 72.0
    doc = fitz.open(str(pdf_path))
    try:
        page = doc.load_page(page_number - 1)
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
        arr = np.frombuffer(pix.samples, dtype=np.uint8)
        arr = arr.reshape(pix.height, pix.width, pix.n)
        if pix.n == 1:
            arr = cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
        elif pix.n == 4:
            arr = cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
        else:
            arr = arr[:, :, ::-1]
        return np.ascontiguousarray(arr)
    finally:
        doc.close()

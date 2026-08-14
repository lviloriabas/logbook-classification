"""Carga de PDFs escaneados a imágenes (PyMuPDF)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from collections.abc import Iterable
from typing import List, TYPE_CHECKING

import pymupdf as fitz  # PyMuPDF
from loguru import logger

if TYPE_CHECKING:
    import numpy as np

    from app.templates.schema import FieldTemplate
    from app.vision.alignment import TransformResult


@dataclass(frozen=True)
class RenderedRegion:
    """Recorte alineado de una página y su ubicación en el lienzo completo.

    ``rect`` usa coordenadas normalizadas del lienzo *ya alineado*. Esto
    permite renderizar solo una banda a alta resolución y seguir usando las
    geometrías dinámicas de la plantilla sin reservar una página de 600 DPI.
    """

    image: "np.ndarray"
    rect: tuple[float, float, float, float]
    dpi: int

    def local_field(self, field: "FieldTemplate") -> "FieldTemplate":
        """Convierte un campo global a coordenadas locales del recorte."""
        x, y, width, height = self.rect
        if width <= 0 or height <= 0:
            return field
        return field.model_copy(update={
            "x": (field.x - x) / width,
            "y": (field.y - y) / height,
            "w": field.w / width,
            "h": field.h / height,
        })

    def local_x(self, normalized_x: float) -> int:
        """Lleva una coordenada X global normalizada a píxeles locales."""
        x, _y, width, _height = self.rect
        return int(round((normalized_x - x) * self.image.shape[1] / width))


class PdfPageRenderer:
    """Mantiene un PDF abierto durante todas las etapas de procesamiento."""

    def __init__(self, pdf_path: Path | str) -> None:
        self.pdf_path = Path(pdf_path)
        if not self.pdf_path.exists():
            raise FileNotFoundError(f"PDF no encontrado: {self.pdf_path}")
        self.document = fitz.open(str(self.pdf_path))

    def __enter__(self) -> "PdfPageRenderer":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def close(self) -> None:
        if self.document is not None:
            self.document.close()
            self.document = None

    def page_count(self) -> int:
        return len(self.document)

    def detect_dpi(self, default: int = 200) -> int:
        if not len(self.document):
            return default
        page = self.document.load_page(0)
        width_pts = max(page.mediabox.width, 1.0)
        for img in page.get_images(full=True):
            pix = self.document.extract_image(img[0])
            dpi = round(pix.get("width", 0) * 72.0 / width_pts)
            if 72 <= dpi <= 600:
                return dpi
        return default

    def render_page(self, page_number: int, dpi: int = 150) -> "np.ndarray":
        """Renderiza una página completa en BGR."""
        import cv2
        import numpy as np

        zoom = dpi / 72.0
        page = self.document.load_page(page_number - 1)
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
        return _pixmap_to_bgr(pix, cv2, np)

    def render_aligned_region(
        self,
        page_number: int,
        dpi: int,
        rect: tuple[float, float, float, float],
        base_shape: tuple[int, ...],
        deskew_angle: float = 0.0,
        alignment: "TransformResult | None" = None,
    ) -> RenderedRegion:
        """Renderiza y alinea únicamente ``rect`` a alta resolución.

        Se compone la misma rotación de deskew y transformación de similitud
        usadas sobre la página base. El ``clip`` de PyMuPDF se calcula con la
        inversa de esa matriz, por lo que solo se rasterizan los píxeles que
        pueden terminar dentro de la banda solicitada.
        """
        import cv2
        import numpy as np

        page = self.document.load_page(page_number - 1)
        zoom = dpi / 72.0
        full_width = max(1, int(round(page.rect.width * zoom)))
        full_height = max(1, int(round(page.rect.height * zoom)))
        x, y, width, height = rect
        x0 = max(0, min(full_width - 1, int(round(x * full_width))))
        y0 = max(0, min(full_height - 1, int(round(y * full_height))))
        x1 = max(x0 + 1, min(full_width, int(round((x + width) * full_width))))
        y1 = max(y0 + 1, min(full_height, int(round((y + height) * full_height))))

        # Deskew rota alrededor del centro del lienzo completo.
        deskew = cv2.getRotationMatrix2D(
            (full_width / 2.0, full_height / 2.0), deskew_angle, 1.0
        ).astype(np.float64)
        deskew3 = np.vstack([deskew, (0.0, 0.0, 1.0)])

        align3 = np.eye(3, dtype=np.float64)
        if alignment is not None:
            radians = np.deg2rad(float(alignment.rot))
            cosine, sine = np.cos(radians), np.sin(radians)
            base_height, base_width = base_shape[:2]
            align3[:2] = np.array([
                [alignment.scale * cosine, -alignment.scale * sine,
                 alignment.tx * full_width / max(base_width, 1)],
                [alignment.scale * sine, alignment.scale * cosine,
                 alignment.ty * full_height / max(base_height, 1)],
            ])
        transform = align3 @ deskew3
        inverse = np.linalg.inv(transform)

        target_corners = np.array([
            [x0, y0, 1.0], [x1, y0, 1.0],
            [x1, y1, 1.0], [x0, y1, 1.0],
        ]).T
        raw_corners = inverse @ target_corners
        margin = 4
        rx0 = max(0, int(np.floor(raw_corners[0].min())) - margin)
        ry0 = max(0, int(np.floor(raw_corners[1].min())) - margin)
        rx1 = min(full_width, int(np.ceil(raw_corners[0].max())) + margin)
        ry1 = min(full_height, int(np.ceil(raw_corners[1].max())) + margin)
        clip = fitz.Rect(rx0 / zoom, ry0 / zoom, rx1 / zoom, ry1 / zoom)
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=clip)
        source = _pixmap_to_bgr(pix, cv2, np)

        local = transform[:2].copy()
        source_origin = np.array([float(pix.x), float(pix.y)])
        local[:, 2] += transform[:2, :2] @ source_origin
        local[:, 2] -= np.array([float(x0), float(y0)])
        aligned = cv2.warpAffine(
            source,
            local.astype(np.float32),
            (x1 - x0, y1 - y0),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=(255, 255, 255),
        )
        normalized = (
            x0 / full_width,
            y0 / full_height,
            (x1 - x0) / full_width,
            (y1 - y0) / full_height,
        )
        return RenderedRegion(np.ascontiguousarray(aligned), normalized, dpi)


def _pixmap_to_bgr(pix, cv2, np):
    """Convierte un Pixmap de PyMuPDF a un array BGR contiguo."""
    arr = np.frombuffer(pix.samples, dtype=np.uint8)
    arr = arr.reshape(pix.height, pix.width, pix.n)
    if pix.n == 1:
        arr = cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
    elif pix.n == 4:
        arr = cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
    else:
        arr = arr[:, :, ::-1]
    return np.ascontiguousarray(arr)


class PdfDocumentCache:
    """Mantiene abiertos los PDFs fuente durante una exportación.

    Abrir un documento por cada página es innecesariamente costoso y además
    obliga a los exportadores a renderizar la página para volver a crearla.
    Esta caché permite copiar las páginas originales directamente.
    """

    def __init__(self, pdf_paths: Iterable[Path | str]) -> None:
        self._paths = {
            str(Path(pdf_path).resolve()) for pdf_path in pdf_paths
        }
        self._documents: dict[str, fitz.Document] = {}

    def __enter__(self) -> "PdfDocumentCache":
        try:
            for path in self._paths:
                self._documents[path] = fitz.open(path)
        except Exception:
            self.close()
            raise
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def close(self) -> None:
        for document in self._documents.values():
            document.close()
        self._documents.clear()

    def get(self, pdf_path: Path | str) -> fitz.Document:
        """Devuelve el documento abierto correspondiente a ``pdf_path``."""
        key = str(Path(pdf_path).resolve())
        try:
            return self._documents[key]
        except KeyError as exc:
            raise KeyError(f"PDF no abierto en la caché: {pdf_path}") from exc


def copy_pdf_pages(
    page_refs: Iterable[tuple[Path | str, int]],
    output_path: Path | str,
    sources: PdfDocumentCache | None = None,
) -> Path:
    """Copia páginas fuente a un PDF sin rasterizarlas ni recomprimirlas."""
    refs = list(page_refs)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if not refs:
        output_path.unlink(missing_ok=True)
        return output_path
    document = fitz.open()

    def copy_from_sources(source_cache: PdfDocumentCache) -> None:
        pending: tuple[str, int, int] | None = None

        def flush() -> None:
            nonlocal pending
            if pending is None:
                return
            source_path, first_page, last_page = pending
            document.insert_pdf(
                source_cache.get(source_path),
                from_page=first_page - 1,
                to_page=last_page - 1,
            )
            pending = None

        for pdf_path, page_number in refs:
            source_path = str(Path(pdf_path).resolve())
            if (pending is not None
                    and pending[0] == source_path
                    and page_number == pending[2] + 1):
                pending = (source_path, pending[1], page_number)
            else:
                flush()
                pending = (source_path, page_number, page_number)
        flush()

    try:
        if sources is None:
            with PdfDocumentCache(pdf_path for pdf_path, _ in refs) as cache:
                copy_from_sources(cache)
        else:
            copy_from_sources(sources)
        document.save(str(output_path), deflate=True)
    finally:
        document.close()
    return output_path


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
    with PdfPageRenderer(pdf_path) as renderer:
        return renderer.detect_dpi(default)


def page_count(pdf_path: Path) -> int:
    """Número de páginas del PDF."""
    with PdfPageRenderer(pdf_path) as renderer:
        return renderer.page_count()


def render_page(pdf_path: Path, page_number: int,
                dpi: int = 150) -> np.ndarray:
    """Renderiza una sola página del PDF (para vistas previas)."""
    with PdfPageRenderer(pdf_path) as renderer:
        return renderer.render_page(page_number, dpi)

"""Exportación compatible del PDF de debug sin modificar las bitácoras.

La información de validación y los bounding boxes permanece disponible en
CSV, JSON y logs. El PDF conserva únicamente las páginas fuente para que
ningún archivo exportado contenga bandas, textos o recuadros superpuestos.
"""

from __future__ import annotations

from pathlib import Path
from typing import List

from loguru import logger

from app.models.schemas import ValidationReport
from app.templates.schema import Template
from app.utils.io import unique_path
from app.vision.pdf_loader import copy_pdf_pages


def write_debug_pdf(
    reports: List[ValidationReport],
    template: Template,
    output_path: Path,
    dpi: int = 150,
    legend: bool = False,
    crop_padding: float = 0.01,
) -> Path:
    """Genera ``debug.pdf`` con las páginas fuente sin anotaciones.

    ``template``, ``dpi``, ``legend`` y ``crop_padding`` se conservan en la
    firma para no romper llamadas existentes, pero no se dibuja información
    sobre las páginas exportadas.

    Un ``debug.pdf`` anterior en la misma carpeta se conserva: el nuevo se
    escribe con sufijo numérico.
    """
    del template, dpi, legend, crop_padding
    output_path = unique_path(Path(output_path))
    page_refs = [
        (report.pdf_path, page.page_number)
        for report in reports
        for page in report.pages
    ]
    copy_pdf_pages(page_refs, output_path)
    total_pages = len(page_refs)
    logger.info(f"[DebugPDF] Generado: {output_path} "
                f"({total_pages} páginas de bitácoras)")
    return output_path

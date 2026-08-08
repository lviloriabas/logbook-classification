"""Generación de reportes JSON."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from loguru import logger

from app.models.schemas import ValidationReport


class JsonReporter:
    """Escribe el reporte completo de validación en formato JSON."""

    def write(self, report: ValidationReport, path: Path) -> Path:
        """Guarda el reporte como JSON legible.

        Args:
            report: Reporte de validación.
            path: Ruta de salida.

        Returns:
            La ruta del archivo generado.
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(
                report.model_dump(mode="json"), fh,
                indent=2, ensure_ascii=False,
            )
        logger.info(f"Reporte JSON generado: {path}")
        return path

    def write_consolidated(
        self,
        reports: List[ValidationReport],
        path: Path,
        corrida: Optional[str] = None,
    ) -> Path:
        """Guarda todos los reportes de la corrida en un único JSON.

        El archivo lleva el mismo nombre que el CSV consolidado de la
        corrida y contiene la lista de reportes (uno por bitácora).

        Args:
            reports: Reportes de validación de la corrida.
            path: Ruta de salida (``datos/<nombre del CSV>.json``).
            corrida: Nombre de la corrida (stem del CSV).

        Returns:
            La ruta del archivo generado.
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "corrida": corrida,
            "generado": datetime.now().isoformat(timespec="seconds"),
            "total_bitacoras": len(reports),
            "reportes": [r.model_dump(mode="json") for r in reports],
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
        logger.info(f"Reporte JSON consolidado generado: {path} "
                    f"({len(reports)} bitácora(s))")
        return path

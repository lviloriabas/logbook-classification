"""Generación de reportes JSON."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import List, Mapping, Optional

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
        dia_leido: Optional[bool] = None,
    ) -> Path:
        """Guarda todos los reportes de la ejecución en un único JSON.

        El archivo lleva el mismo nombre que el CSV consolidado de la
        ejecución y contiene la lista de reportes (uno por bitácora).

        Args:
            reports: Reportes de validación de la ejecución.
            path: Ruta de salida (``datos/<nombre del CSV>.json``).
            ejecución: Nombre de la ejecución (stem del CSV).
            dia_leido: Si la ejecución leyó el día de la fecha. ``None``
                no escribe la clave.

        Returns:
            La ruta del archivo generado.
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "corrida": corrida,
            "generado": datetime.now().isoformat(timespec="seconds"),
            "total_bitacoras": len(reports),
            # Si la ejecución leyó el día. Una que fue a fin de mes no lo
            # leyó y no se puede volver a representar con el día exacto: es
            # lo que consulta la ventana de AirVault para apagar esa opción.
            # Se omite en las ejecuciones anteriores a esta decisión, que
            # siempre lo leyeron.
            **({} if dia_leido is None else {"dia_leido": bool(dia_leido)}),
            "reportes": [r.model_dump(mode="json") for r in reports],
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
        logger.info(f"Reporte JSON consolidado generado: {path} "
                    f"({len(reports)} bitácora(s))")
        return path

    @staticmethod
    def relocate_consolidated_sources(
        path: Path, moved: Mapping[Path, Path]
    ) -> int:
        """Guarda en el JSON el nombre definitivo dentro de ``processed``.

        Las salidas se escriben antes de archivar los originales. Si un nombre
        ya existe, el archivo recién procesado termina como ``-2`` o ``-3``;
        conservar la ruta anterior haría que el visor histórico abriera el PDF
        de otra ejecución. También conserva ``source_name`` para que el CSV no
        cambie. El JSON se reemplaza de forma atómica para no dejarlo a medias.
        """
        path = Path(path)
        if not moved or not path.is_file():
            return 0

        def key(value: Path | str) -> str:
            try:
                return str(Path(value).resolve()).casefold()
            except OSError:
                return str(Path(value)).casefold()

        destinations = {
            key(source): str(destination)
            for source, destination in moved.items()
        }
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)

        changed = 0
        for report in payload.get("reportes", []):
            if not isinstance(report, dict):
                continue
            original = report.get("pdf_path", "")
            destination = destinations.get(key(original))
            if destination is None:
                continue
            report["source_name"] = (
                report.get("source_name") or Path(str(original)).name
            )
            report["pdf_path"] = destination
            changed += 1
        if not changed:
            return 0

        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        logger.info(
            f"Rutas de origen actualizadas en {path}: {changed} archivo(s)"
        )
        return changed

"""Generación de reportes CSV (compatibles con Excel)."""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import List, Optional, Union

from loguru import logger

from app.models.schemas import PageResult, ValidationReport
from app.templates.schema import Template
from app.utils.postprocess import MESES

_DATE_RE = re.compile(r"^\d{4}/\d{2}/\d{2}$")
_MATRICULA_RE = re.compile(r"^HP-\d{4}(CMP|WWP)$")
_DAY_RE = re.compile(r"^\d{1,2}$")
_MONTH_RE = re.compile(r"^(\d{1,2}|" + "|".join(MESES) + r")$")


class CsvReporter:
    """Escribe el reporte de validación en CSV ancho (en inglés).

    Una fila por página del PDF:
        file, page, <field>, <field>_conf, ..., date, time_ms

    - ``file``: nombre del PDF del que proviene la página.
    - ``date``: fecha normalizada (YYYY/MM/dd) combinando day/month/year.
    - ``time_ms``: tiempo de procesamiento solo de la página (el total
      del PDF solo se imprime en consola).

    Puertas finales de formato: los valores de matrícula, día, mes y año
    solo se escriben si cumplen su formato canónico (HP-XXXXCMP/WWP,
    dígitos, mes canónico, año de 2-4 dígitos); cualquier lectura basura
    del OCR queda como celda vacía.
    """

    def write(
        self,
        report_or_reports: Union[ValidationReport, List[ValidationReport]],
        path: Path,
        template: Optional[Template] = None,
    ) -> Path:
        """Guarda el reporte como CSV (UTF-8 con BOM para Excel).

        Args:
            report_or_reports: Reporte único o lista de reportes (uno por
                PDF). Con una lista se genera una tabla consolidada.
            path: Ruta de salida.
            template: Plantilla usada (define el orden y los campos). Si no
                se provee, se usan los campos de la primera página no vacía.

        Returns:
            La ruta del archivo generado.
        """
        reports = (
            [report_or_reports]
            if isinstance(report_or_reports, ValidationReport)
            else list(report_or_reports)
        )
        if not reports:
            raise ValueError("No hay reportes para generar el CSV")

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        fields = self.fields_for(reports, template)
        columns = self.columns_for_fields(fields)

        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            for report in reports:
                for page in report.pages:
                    writer.writerow(self.row_for_page(report, page, fields))

        logger.info(f"Reporte CSV generado: {path} "
                    f"({sum(len(r.pages) for r in reports)} páginas)")
        return path

    @classmethod
    def columns_for_fields(cls, fields: List[str]) -> List[str]:
        """Devuelve las columnas del CSV para una lista de campos."""
        columns = ["file", "page"]
        for field_id in fields:
            columns.extend([field_id, f"{field_id}_conf"])
        columns.extend(["date", "time_ms"])
        return columns

    @classmethod
    def columns_for(
        cls,
        reports: List[ValidationReport],
        template: Optional[Template] = None,
    ) -> List[str]:
        """Devuelve las columnas que usaría ``write`` para esos reportes."""
        fields = cls.fields_for(reports, template)
        return cls.columns_for_fields(fields)

    @classmethod
    def fields_for(
        cls,
        reports: List[ValidationReport],
        template: Optional[Template] = None,
    ) -> List[str]:
        """Devuelve los identificadores de campos usados por el CSV."""
        return cls._columns_fields(reports, template)

    @classmethod
    def row_for_page(
        cls,
        report: ValidationReport,
        page: PageResult,
        fields: List[str],
    ) -> dict[str, object]:
        """Construye la fila CSV correspondiente a una página."""
        row: dict[str, object] = {
            "file": Path(report.pdf_path).name,
            "page": page.page_number,
        }
        by_id = {field.field_id: field for field in page.fields}

        for field_id in fields:
            result = by_id.get(field_id)
            if result is None:
                row[field_id] = ""
                row[f"{field_id}_conf"] = ""
                continue
            row[field_id] = cls._gated_value(field_id, result.value)
            row[f"{field_id}_conf"] = round(result.confidence, 3)

        row["date"] = cls._date(page)
        row["time_ms"] = round(page.processing_ms, 1)
        return row

    @staticmethod
    def _gated_value(field_id: str, value: Optional[str]) -> str:
        """Aplica la puerta de formato canónico al valor del campo.

        Solo matrícula, día, mes y año tienen puerta; el resto de campos
        se escribe tal cual. Un valor que no cumple el formato canónico
        se descarta (celda vacía).
        """
        if not value:
            return ""
        if field_id == "matricula":
            return value if _MATRICULA_RE.fullmatch(value) else ""
        if field_id == "day":
            return value if _DAY_RE.fullmatch(value) else ""
        if field_id == "month":
            return value if _MONTH_RE.fullmatch(value) else ""
        if field_id == "year":
            if len(value) == 2 and value.isdigit():
                return value
            if len(value) == 4 and value.isdigit() and 2000 <= int(value) <= 2100:
                return value
            return ""
        return value

    @staticmethod
    def _columns_fields(
        reports: List[ValidationReport], template: Optional[Template]
    ) -> List[str]:
        if template is not None:
            return [f.id for f in template.fields]
        for report in reports:
            for page in report.pages:
                if page.fields:
                    return [f.field_id for f in page.fields]
        return []

    @staticmethod
    def _date(page) -> str:
        """Fecha normalizada (YYYY/MM/dd) de la página, si está disponible."""
        if page.date and _DATE_RE.match(page.date):
            return page.date
        return ""

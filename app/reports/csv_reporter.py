"""Generación de reportes CSV (compatibles con Excel)."""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import List, Optional, Union

from loguru import logger

from app.models.schemas import PageResult, ValidationReport
from app.templates.schema import FieldType, Template
from app.utils.postprocess import MESES

_DATE_RE = re.compile(r"^\d{4}/\d{2}/\d{2}$")
_MATRICULA_RE = re.compile(r"^HP-\d{4}(CMP|WWP)$")
_DAY_RE = re.compile(r"^\d{1,2}$")
_MONTH_RE = re.compile(r"^(\d{1,2}|" + "|".join(MESES) + r")$")
# Celdas de carácter de la banda de fecha (una casilla por campo).
_CHAR_DIGIT_CELL_RE = re.compile(r"^(day|year)_\d+$")
_CHAR_LETTER_CELL_RE = re.compile(r"^month_\d+$")


class CsvReporter:
    """Escribe el reporte de validación en CSV ancho (en inglés).

    Una fila por página del PDF:
        file, page, <field>, <field>_conf, <field>_status,
        <field>_comment, <field>_source, ..., date, time_ms

    - ``file``: nombre del PDF del que proviene la página.
    - ``date``: fecha normalizada (YYYY/MM/dd) combinando day/month/year.
    - ``time_ms``: tiempo de procesamiento solo de la página (el total
      del PDF solo se imprime en consola).

    Las columnas ``<field>_status`` y ``<field>_comment`` se omiten para
    los campos de firma: quedan ``<field>``, ``<field>_conf`` y
    ``<field>_source``.

    Puertas finales de formato: los valores de matrícula, día, mes, año y
    celdas de carácter solo se escriben si cumplen su formato canónico
    (HP-XXXXCMP/WWP, dígitos, mes canónico, año de 2-4 dígitos, un dígito
    en day_X/year_X, una letra en month_X); cualquier lectura basura del
    OCR queda como celda vacía.
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
        skip_ids = self._skip_ids(reports, template)
        columns = self.columns_for_fields(fields, skip_ids=skip_ids)

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
    def columns_for_fields(cls, fields: List[str],
                           skip_ids: frozenset[str] = frozenset()) -> List[str]:
        """Devuelve las columnas del CSV para una lista de campos.

        Args:
            fields: Identificadores de campos en el orden de las columnas.
            skip_ids: Campos a los que no se les emiten columnas ``_status``
                ni ``_comment`` (las firmas, p. ej.).
        """
        columns = ["file", "page"]
        for field_id in fields:
            columns.extend([field_id, f"{field_id}_conf"])
            if field_id not in skip_ids:
                columns.extend([
                    f"{field_id}_status",
                    f"{field_id}_comment",
                ])
            columns.append(f"{field_id}_source")
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
        skip_ids = cls._skip_ids(reports, template)
        return cls.columns_for_fields(fields, skip_ids=skip_ids)

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
                row[f"{field_id}_status"] = ""
                row[f"{field_id}_comment"] = ""
                row[f"{field_id}_source"] = ""
                continue
            row[field_id] = cls._gated_value(field_id, result.value)
            row[f"{field_id}_conf"] = round(result.confidence, 3)
            row[f"{field_id}_status"] = result.status.value
            row[f"{field_id}_comment"] = result.comment
            row[f"{field_id}_source"] = result.source

        row["date"] = cls._date(page)
        row["time_ms"] = round(page.processing_ms, 1)
        return row

    @staticmethod
    def _gated_value(field_id: str, value: Optional[str]) -> str:
        """Aplica la puerta de formato canónico al valor del campo.

        Solo matrícula, día, mes, año y las celdas de carácter tienen
        puerta; el resto de campos se escribe tal cual. Un valor que no
        cumple el formato canónico se descarta (celda vacía).
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
        if _CHAR_DIGIT_CELL_RE.match(field_id):
            return value if value.isdigit() and len(value) == 1 else ""
        if _CHAR_LETTER_CELL_RE.match(field_id):
            return value if value.isalpha() and len(value) == 1 else ""
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

    @classmethod
    def _skip_ids(
        cls,
        reports: List[ValidationReport],
        template: Optional[Template] = None,
    ) -> frozenset[str]:
        """Identificadores que no llevan columnas ``_status``/``_comment``."""
        if template is not None:
            return frozenset(
                f.id for f in template.fields
                if f.type is FieldType.SIGNATURE
            )
        ids = set()
        for report in reports:
            for page in report.pages:
                ids.update(
                    f.field_id for f in page.fields
                    if f.field_type == "signature"
                )
        return frozenset(ids)

    @staticmethod
    def _date(page) -> str:
        """Fecha normalizada (YYYY/MM/dd) de la página, si está disponible."""
        if page.date and _DATE_RE.match(page.date):
            return page.date
        return ""

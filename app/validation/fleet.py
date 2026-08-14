"""Verificación opcional de matrículas contra la lista de flota."""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from app.models.schemas import Status, ValidationReport
from app.utils.fleet import load_fleet


def verify_reports_against_fleet(
    reports: list[ValidationReport], fleet_path: Path
) -> None:
    """Marca para revisión las matrículas válidas que no están en la flota.

    La comprobación es deliberadamente no destructiva: conserva el texto leído
    y solo añade ``WARNING`` y un comentario cuando existe una lista usable.
    """
    allowed = set(load_fleet(Path(fleet_path)))
    if not allowed:
        logger.warning(
            f"Verificación de flota activa pero la lista está vacía o no existe: {fleet_path}"
        )
        return
    for report in reports:
        for page in report.pages:
            field = next(
                (item for item in page.fields if item.field_id == "matricula"),
                None,
            )
            value = (field.value or "").strip().upper() if field else ""
            if not value or value in allowed:
                continue
            note = f"Matrícula no encontrada en la lista de flota: {value}"
            field.status = Status.ERROR if field.status is Status.ERROR else Status.WARNING
            field.comment = f"{field.comment} | {note}".strip(" |")
            page.status = max(
                (item.status for item in page.fields),
                key={Status.OK: 0, Status.WARNING: 1, Status.ERROR: 2}.get,
                default=page.status,
            )
        counts = {"ok_pages": 0, "warning_pages": 0, "error_pages": 0}
        for page in report.pages:
            if page.status is Status.OK:
                counts["ok_pages"] += 1
            elif page.status is Status.WARNING:
                counts["warning_pages"] += 1
            else:
                counts["error_pages"] += 1
        report.summary.update(
            total_pages=len(report.pages),
            blank_pages=sum(1 for page in report.pages if page.blank),
            **counts,
        )

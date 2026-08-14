"""Verificación opcional de matrículas contra la lista de flota."""

from __future__ import annotations

from pathlib import Path
import re

from loguru import logger

from app.models.schemas import Status, ValidationReport
from app.utils.fleet import load_fleet


_MATRICULA_RE = re.compile(r"^HP-(\d{4})(CMP|WWP)$")


def _unique_nearby_fleet_match(value: str, allowed: set[str]) -> str | None:
    """Devuelve una única matrícula de flota a un dígito de distancia.

    Restringir la comparación a igual prefijo/sufijo y exactamente una
    posición distinta permite corregir el 2/7 manuscrito sin convertir una
    lista incompleta en una regla agresiva de sustitución.
    """
    observed = _MATRICULA_RE.fullmatch(value)
    if observed is None:
        return None
    digits, suffix = observed.groups()
    candidates: list[str] = []
    for candidate in allowed:
        match = _MATRICULA_RE.fullmatch(candidate)
        if match is None or match.group(2) != suffix:
            continue
        distance = sum(left != right for left, right in zip(digits, match.group(1)))
        if distance == 1:
            candidates.append(candidate)
    return candidates[0] if len(candidates) == 1 else None


def verify_reports_against_fleet(
    reports: list[ValidationReport], fleet_path: Path
) -> None:
    """Valida y, si no hay ambigüedad, corrige matrículas con la flota.

    Una lectura canónica fuera de lista solo se corrige cuando hay exactamente
    una matrícula del mismo tipo a un dígito de distancia. El valor OCR queda
    en ``alternatives`` y la página sigue en WARNING para que la corrección sea
    auditable. Si la lista no decide de forma única, solo se marca para revisión.
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
            fleet_match = _unique_nearby_fleet_match(value, allowed)
            if fleet_match is not None:
                if value not in field.alternatives:
                    field.alternatives.append(value)
                field.value = fleet_match
                field.source = "fleet_validation"
                field.inference_method = "fleet_unique_hamming"
                note = (
                    f"Matrícula corregida de {value} a {fleet_match} mediante "
                    "coincidencia única con la flota"
                )
            else:
                note = f"Matrícula no encontrada en la lista de flota: {value}"
            field.status = Status.WARNING
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

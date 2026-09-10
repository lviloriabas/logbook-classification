"""Clasificación de matrículas contra la lista de flota."""

from __future__ import annotations

from pathlib import Path
import re

from loguru import logger

from app.models.schemas import FieldResult, Status, ValidationReport
from app.utils.fleet import load_fleet
from app.validation.book_corrector import _recompute_summary
from app.validation.fleet_match import (
    SUFFIX_COST as _SUFFIX_COST,
    digit_cost as _digit_cost,
    reading_cost,
)
from app.validation.grouping import book_key
from app.validation.page_status import recompute_page_status


_MATRICULA_RE = re.compile(r"^HP-(\d{4})(CMP|WWP)$")

# Los costos por cifra (trazos confundibles, sufijo) viven en
# ``app.validation.fleet_match``, que también los usa el corrector del libro.


def _distance(observed: re.Match[str], expected: re.Match[str]) -> int:
    """Cuánto hay que forzar la lectura para convertirla en el candidato."""
    cost = sum(
        _digit_cost(left, right)
        for left, right in zip(observed.group(1), expected.group(1))
    )
    if observed.group(2) != expected.group(2):
        cost += _SUFFIX_COST
    return cost


def _nearest_fleet_match(
    value: str, allowed: set[str]
) -> tuple[str | None, list[str]]:
    """Avión de la flota más parecido a ``value``.

    Devuelve ``(ganador, empatados)``. La lista de flota se mantiene completa,
    así que una lectura que no está en ella es un error de OCR y el avión más
    parecido es la respuesta. Solo cuando dos aviones quedan exactamente a la
    misma distancia no hay "el más parecido": ahí no se elige, porque acertar
    sería suerte, y las opciones se dejan escritas para quien revise.

    Una lectura sin formato de matrícula no se compara con nadie: no tiene
    los cuatro dígitos que sostienen la comparación, así que cualquier avión
    de la flota estaría igual de lejos.
    """
    observed = _MATRICULA_RE.fullmatch(value)
    if observed is None:
        return None, []
    ranked = []
    for candidate in allowed:
        expected = _MATRICULA_RE.fullmatch(candidate)
        if expected is not None:
            ranked.append((_distance(observed, expected), candidate))
    if not ranked:
        return None, []
    ranked.sort()
    best = ranked[0][0]
    tied = [candidate for distance, candidate in ranked if distance == best]
    return (tied[0] if len(tied) == 1 else None), tied


def _readings(field: FieldResult) -> list[str]:
    """Lo que leyó el campo: el texto crudo del OCR y las alternativas.

    En las alternativas dejan los correctores lo que la página leyó antes de
    imponerle la matrícula del libro.
    """
    return [text for text in (field.raw_value, *field.alternatives) if text]


def _break_tie(tied: list[str], readings: list[str]) -> str | None:
    """Entre aviones igual de parecidos, el que mejor explica el libro.

    El valor que empata es uno solo, pero cada página del libro conserva lo
    que leyó. El avión al que menos cuesta llegar desde todas esas lecturas
    es el que las explica; si siguen empatados, no se elige.
    """
    scored = []
    for candidate in tied:
        costs = [reading_cost(text, candidate) for text in readings]
        scored.append((sum(cost for cost in costs if cost is not None), candidate))
    scored.sort()
    if readings and len(scored) > 1 and scored[0][0] < scored[1][0]:
        return scored[0][1]
    return None


def verify_reports_against_fleet(
    reports: list[ValidationReport], fleet_path: Path
) -> None:
    """Reclasifica cada matrícula fuera de lista como el avión más parecido.

    La lista de flota es el catálogo completo de aviones, así que una lectura
    canónica que no aparezca en ella no existe como avión: se reemplaza por la
    matrícula más parecida de la flota. El valor leído queda en
    ``alternatives`` y la página sigue en WARNING para que la reclasificación
    sea auditable.

    Cuando no hay un avión más parecido (dos quedan a la misma distancia, o
    la lectura ni siquiera tiene formato de matrícula) la página se queda sin
    matrícula. Antes conservaba la lectura, y esa lectura terminaba abriendo
    en el PDF y en las estadísticas una bitácora de un avión que no existe;
    ahora cae en «Revisar», que es donde una persona decide de qué avión era.
    """
    allowed = set(load_fleet(Path(fleet_path)))
    if not allowed:
        logger.warning(
            "Verificación de flota activa pero la lista está vacía o no "
            f"existe: {fleet_path}. Sin lista no se puede reclasificar "
            "ninguna matrícula."
        )
        return
    def matricula_of(page) -> FieldResult | None:
        return next(
            (item for item in page.fields if item.field_id == "matricula"),
            None,
        )

    # Se toman antes de reclasificar nada: al hacerlo, cada página guarda en
    # sus alternativas el valor del libro, que no es una lectura suya.
    readings_by_book: dict[tuple[str, str], list[str]] = {}
    for report in reports:
        for page in report.pages:
            key = book_key(page)
            field = matricula_of(page)
            if key is not None and field is not None:
                readings_by_book.setdefault(key, []).extend(_readings(field))

    for report in reports:
        for page in report.pages:
            field = matricula_of(page)
            if field is None:
                continue
            value = (field.value or "").strip().upper()
            if not value or value in allowed:
                continue
            fleet_match, tied = _nearest_fleet_match(value, allowed)
            tie_broken = False
            if fleet_match is None and len(tied) > 1:
                key = book_key(page)
                fleet_match = _break_tie(
                    tied,
                    readings_by_book.get(key, [])
                    if key is not None else _readings(field),
                )
                tie_broken = fleet_match is not None
            if value not in field.alternatives:
                field.alternatives.append(value)
            if fleet_match is not None:
                field.value = fleet_match
                field.source = "fleet_validation"
                field.inference_method = "fleet_nearest_match"
                # Nadie leyo este avion: se eligio por parecido con lo que
                # se leyo. Sin respaldo de lectura, aunque el consenso del
                # libro detras fuera unanime, porque lo que el libro voto
                # era otra matricula.
                field.votes = 0
                note = (
                    f"Matrícula reclasificada de {value} a {fleet_match}: "
                    + (
                        f"empataba con {', '.join(c for c in tied if c != fleet_match)} "
                        "y la desempatan las lecturas del libro"
                        if tie_broken
                        else "es la más parecida de la lista de flota"
                    )
                )
            else:
                # La lista de flota es el catálogo completo, así que este
                # avión no existe y no se puede elegir uno por él. Dejar la
                # lectura escrita creaba una bitácora de un avión inexistente
                # en el CSV y una sección propia en el PDF; se borra el valor
                # y la página cae en «Revisar», que es donde una persona
                # decide de qué avión era.
                field.value = None
                field.source = "fleet_validation"
                field.inference_method = "fleet_unconfirmed"
                note = (
                    f"Matrícula sin confirmar: {value} queda a la misma "
                    f"distancia de {', '.join(tied)}"
                    if tied
                    else f"Matrícula sin confirmar: {value} no está en la "
                         "lista de flota y no se parece a ningún avión de ella"
                )
            field.status = Status.WARNING
            field.comment = f"{field.comment} | {note}".strip(" |")
            recompute_page_status(page)
        _recompute_summary(report)

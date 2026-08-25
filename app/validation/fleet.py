"""Clasificación de matrículas contra la lista de flota."""

from __future__ import annotations

from pathlib import Path
import re

from loguru import logger

from app.models.schemas import Status, ValidationReport
from app.utils.fleet import load_fleet
from app.validation.book_corrector import _recompute_summary
from app.validation.page_status import recompute_page_status


_MATRICULA_RE = re.compile(r"^HP-(\d{4})(CMP|WWP)$")

# Pares de dígitos que el trazo manuscrito de estas bitácoras confunde de
# verdad: el 1 sin base contra el 7 con travesaño, el 2 mal cerrado contra el
# 7, el 3 contra el 8 cuando el lazo se cierra, el 0 contra el 6 y el 9 según
# dónde arranque el trazo. Cambiar uno de estos cuesta menos que cambiar una
# cifra que no se le parece, así que entre dos aviones de la flota que están
# a la misma cantidad de dígitos de distancia gana el que solo pide el trazo
# confundible, que es el error que de verdad comete el reconocedor.
_CONFUSABLE_DIGITS = frozenset({
    "17", "27", "12", "14", "47", "49", "07",
    "38", "35", "58", "56", "68", "08", "06", "09",
})
# Costos enteros: comparar distancias en float haría que 0.6+0.6 no empatara
# exacto con 1.2 y un empate real se resolvería por ruido de coma flotante.
_DIFFERENT_DIGIT_COST = 10
_CONFUSABLE_DIGIT_COST = 6
# El sufijo no se lee de la página: ``apply_postprocess`` lo deduce del número
# con su propia lista de aviones WWP. Por eso cuesta menos que un dígito: si la
# flota trae un WWP que esa lista no conoce, la flota manda y corrige el sufijo.
_SUFFIX_COST = 5


def _digit_cost(left: str, right: str) -> int:
    if left == right:
        return 0
    pair = "".join(sorted(left + right))
    if pair in _CONFUSABLE_DIGITS:
        return _CONFUSABLE_DIGIT_COST
    return _DIFFERENT_DIGIT_COST


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
    for report in reports:
        for page in report.pages:
            field = next(
                (item for item in page.fields if item.field_id == "matricula"),
                None,
            )
            if field is None:
                continue
            value = (field.value or "").strip().upper()
            if not value or value in allowed:
                continue
            fleet_match, tied = _nearest_fleet_match(value, allowed)
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
                    "es la más parecida de la lista de flota"
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

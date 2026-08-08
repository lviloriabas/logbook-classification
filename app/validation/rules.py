"""Reglas de validación de campos (configuradas desde la plantilla)."""

from __future__ import annotations

import re

from loguru import logger

from app.core.config import AppConfig
from app.models.schemas import FieldResult, Status
from app.templates.schema import FieldTemplate


def apply_rules(
    result: FieldResult, field: FieldTemplate, config: AppConfig
) -> FieldResult:
    """Aplica las reglas del campo al resultado.

    Reglas soportadas (todas desde el JSON de plantilla):
    - required: valor obligatorio.
    - regex: el valor debe cumplir el patrón.
    - min_length / max_length: longitud del valor.
    - Confianza baja: WARNING (umbral en AppConfig).

    Args:
        result: Resultado del OCR/detección del campo.
        field: Definición del campo con sus reglas.
        config: Configuración global.

    Returns:
        El mismo resultado con status/comment actualizados.
    """
    value = result.value
    has_value = bool(value and value.strip())

    if not has_value:
        if field.required:
            result.status = Status.ERROR
            result.comment = "Required field empty"
        else:
            result.status = Status.WARNING
            result.comment = "Optional field empty"
        return result

    if field.regex:
        pattern = re.compile(field.regex)
        if not pattern.fullmatch(value):
            result.status = Status.ERROR
            result.comment = f"Does not match pattern {field.regex}"
            return result

    length = len(value)
    if field.min_length is not None and length < field.min_length:
        result.status = Status.ERROR
        result.comment = (
            f"Length {length} < minimum {field.min_length}"
        )
        return result
    if field.max_length is not None and length > field.max_length:
        result.status = Status.ERROR
        result.comment = (
            f"Length {length} > maximum {field.max_length}"
        )
        return result

    # Las firmas se validan solo por presencia (valor "true"/"false"):
    # la confianza baja en una firma ausente no debe degradar la página.
    if result.field_type != "signature" and \
            result.confidence < config.confidence_warning:
        if result.status is Status.OK:
            result.status = Status.WARNING
        suffix = f"Low confidence ({result.confidence:.2f})"
        result.comment = (
            f"{result.comment} | {suffix}" if result.comment else suffix
        )

    if result.status is Status.OK:
        result.status = Status.OK
        if not result.comment:
            result.comment = "OK"
    logger.debug(f"Regla {field.id}: {result.status.value} — "
                 f"{result.comment}")
    return result

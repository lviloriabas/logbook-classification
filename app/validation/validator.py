"""Orquestador de validación de páginas completas."""

from __future__ import annotations

from app.core.config import AppConfig
from app.models.schemas import PageResult, Status
from app.templates.schema import Template
from app.validation.rules import apply_rules


def validate_page(page: PageResult, template: Template,
                  config: AppConfig) -> PageResult:
    """Valida todos los campos de una página contra sus reglas.

    Args:
        page: Página con los resultados crudos de cada campo.
        template: Plantilla que define las reglas.
        config: Configuración global.

    Returns:
        La misma página con estados calculados.
    """
    for field_result in page.fields:
        field_template = template.field(field_result.field_id)
        if field_template is None:
            field_result.status = Status.WARNING
            field_result.comment = "Campo no definido en la plantilla"
            continue
        apply_rules(field_result, field_template, config)

    order = {Status.OK: 0, Status.WARNING: 1, Status.ERROR: 2}
    non_char = [
        f for f in page.fields
        if template.field(f.field_id) is None
        or template.field(f.field_id).postprocess != "char"
    ]
    worst = max((f.status for f in non_char),
                key=order.get, default=Status.OK)
    page.status = worst
    return page

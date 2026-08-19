"""Orquestador de validación de páginas completas."""

from __future__ import annotations

from app.core.config import AppConfig
from app.models.schemas import PageResult, Status
from app.templates.schema import Template
from app.validation.page_status import recompute_page_status
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

    recompute_page_status(page)
    return page

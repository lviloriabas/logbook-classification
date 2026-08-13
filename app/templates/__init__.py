"""Sistema de plantillas basado en JSON con coordenadas relativas."""

from app.templates.manager import TEMPLATES_DIR, TemplateManager
from app.templates.schema import FieldTemplate, FieldType, Template

__all__ = [
    "TEMPLATES_DIR",
    "TemplateManager",
    "Template",
    "FieldTemplate",
    "FieldType",
]

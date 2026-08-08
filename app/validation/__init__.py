"""Validación basada en reglas de plantilla."""

from app.validation.rules import apply_rules
from app.validation.validator import validate_page

__all__ = ["apply_rules", "validate_page"]
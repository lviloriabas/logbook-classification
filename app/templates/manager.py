"""Gestor de plantillas: carga, validación y guardado de JSON."""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

from loguru import logger

from app.templates.schema import Template

EXAMPLES_DIR = Path(__file__).resolve().parent / "examples"


class TemplateManager:
    """Carga y guarda plantillas JSON validadas con pydantic."""

    def __init__(self, templates_dir: Path = EXAMPLES_DIR) -> None:
        self.templates_dir = templates_dir

    def load(self, path: Path) -> Template:
        """Carga una plantilla desde JSON y la valida."""
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Plantilla no encontrada: {path}")
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        template = Template.model_validate(data)
        logger.info(f"Plantilla cargada: {template.name} "
                    f"({len(template.fields)} campos)")
        return template

    def save(self, template: Template, path: Path) -> None:
        """Guarda una plantilla como JSON legible."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(
                template.model_dump(mode="json"), fh, indent=2, ensure_ascii=False
            )
        logger.info(f"Plantilla guardada: {path}")

    def list_templates(self) -> List[Path]:
        """Lista los JSON de plantilla disponibles en el directorio."""
        if not self.templates_dir.exists():
            return []
        return sorted(self.templates_dir.glob("*.json"))

    def list_templates_with_fallback(self) -> List[Path]:
        """Lista plantillas del directorio; si está vacío usa los ejemplos."""
        paths = self.list_templates()
        return paths if paths else sorted(EXAMPLES_DIR.glob("*.json"))

    def load_example(self, name: str) -> Optional[Template]:
        """Carga una plantilla de ejemplo por nombre."""
        path = EXAMPLES_DIR / name
        return self.load(path) if path.exists() else None

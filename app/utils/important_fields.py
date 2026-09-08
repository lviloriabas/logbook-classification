"""Memoria portable de las columnas importantes marcadas en el selector."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from loguru import logger


IMPORTANT_FIELDS_FILENAME = "important_fields.json"
_DEFAULT_KEY = "__default__"

# Columnas que se marcan solas mientras nadie edite la selección: los
# identificadores de la página y los campos críticos de indexación. Vive aquí
# y no en la ventana porque el CSV mínimo lo escriben las dos superficies, y
# una ejecución de línea de comandos tiene que dar el mismo archivo que la
# interfaz sobre la misma plantilla.
_DEFAULT_IMPORTANT = frozenset({
    "file", "page", "date", "time_ms", "dup", "disc", "discrepancia",
    "log_number", "matricula", "flight_number", "pilot_signature",
    "captain_signature", "captain_license",
})


def default_important_columns(columns: Iterable[str]) -> set[str]:
    """Incluye los identificadores y campos críticos disponibles."""
    available = list(columns)
    important = set(_DEFAULT_IMPORTANT)
    important.update(name for name in available if name.endswith("_signature"))
    return set(available).intersection(important)


class ImportantFieldsStore:
    """Guarda por plantilla las columnas marcadas en el selector.

    El archivo vive en la carpeta del programa, igual que ``fleet.json``:
    la selección viaja con la copia portable y no depende del perfil del
    usuario ni del registro de Windows.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def _templates(self) -> dict[str, list[str]]:
        """Contenido válido del archivo; un archivo dañado no bloquea la GUI."""
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {}
        templates = payload.get("templates") if isinstance(payload, dict) else None
        if not isinstance(templates, dict):
            return {}
        return {
            str(name): [str(column) for column in columns]
            for name, columns in templates.items()
            if isinstance(columns, list)
        }

    def load(self, template_name: str | None = None) -> set[str] | None:
        """Selección guardada, o ``None`` si esa plantilla nunca se editó.

        Un conjunto vacío es una respuesta válida: significa que el usuario
        desmarcó todas las columnas, y no debe confundirse con "sin editar".
        """
        stored = self._templates().get(template_name or _DEFAULT_KEY)
        return set(stored) if stored is not None else None

    def save(
        self,
        template_name: str | None,
        columns: Iterable[str],
        scope: Iterable[str] | None = None,
    ) -> set[str]:
        """Registra la selección de una plantilla conservando las demás.

        ``scope`` son las columnas que el selector pudo enseñar. El CSV
        mínimo trae solo las marcadas, así que un selector abierto sobre él
        no sabe nada de las demás: sin esta reserva, editar la lista desde
        una corrida anterior borraba en silencio lo marcado después, y la
        selección nunca terminaba de quedarse guardada. Devuelve la lista
        completa que quedó escrita, que es la que la ventana tiene que
        seguir usando.
        """
        templates = self._templates()
        key = template_name or _DEFAULT_KEY
        selected = set(columns)
        if scope is not None:
            selected.update(set(templates.get(key, ())) - set(scope))
        templates[key] = sorted(selected)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(
                    {"version": 1, "templates": templates},
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        except OSError as exc:  # noqa: BLE001 - preferencia, no dato crítico
            logger.warning(f"No se pudo guardar las columnas importantes: {exc}")
        return selected

"""Formato y lectura de la lista portable de matrícula."""

from __future__ import annotations

import json
import re
from pathlib import Path


FLEET_FILENAME = "fleet.json"
_MATRICULA_RE = re.compile(r"^HP-\d{4}(?:CMP|WWP)$", re.IGNORECASE)


def normalise_matricula(value: str) -> str:
    value = str(value).strip().upper().replace(" ", "")
    if value and not value.startswith("HP-") and value.isdigit():
        value = f"HP-{value}CMP"
    elif value.startswith("HP") and not value.startswith("HP-"):
        value = "HP-" + value[2:]
    return value if _MATRICULA_RE.fullmatch(value) else ""


def load_fleet(path: Path) -> list[str]:
    path = Path(path)
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return []
    values = payload.get("matriculas", []) if isinstance(payload, dict) else payload
    if not isinstance(values, list):
        return []
    result = {normalise_matricula(str(value)) for value in values}
    return sorted(value for value in result if value)

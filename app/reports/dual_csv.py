"""Derivación del CSV mínimo desde el CSV referencial completo."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable


_METADATA_SUFFIXES = ("_conf", "_status", "_comment", "_source")


def default_minimum_columns(columns: Iterable[str]) -> list[str]:
    """Campos de valor e identificadores, sin metadatos técnicos."""
    return [
        column
        for column in columns
        if not any(column.endswith(suffix) for suffix in _METADATA_SUFFIXES)
    ]


def write_minimal_csv(
    complete_path: Path,
    minimal_path: Path,
    selected_columns: Iterable[str] = (),
) -> Path:
    """Filtra columnas sin reconstruir ni mutar el dataset completo."""
    complete_path = Path(complete_path)
    minimal_path = Path(minimal_path)
    with complete_path.open("r", newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        if not reader.fieldnames:
            raise ValueError("El CSV completo no contiene encabezados")
        columns = list(reader.fieldnames)
        selected = set(selected_columns)
        if not selected:
            selected = set(default_minimum_columns(columns))
        selected.update({"file", "page"})
        output_columns = [column for column in columns if column in selected]
        rows = list(reader)

    minimal_path.parent.mkdir(parents=True, exist_ok=True)
    with minimal_path.open("w", newline="", encoding="utf-8-sig") as target:
        writer = csv.DictWriter(
            target, fieldnames=output_columns, extrasaction="ignore"
        )
        writer.writeheader()
        writer.writerows(rows)
    return minimal_path

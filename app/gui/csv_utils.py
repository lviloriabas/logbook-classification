"""Funciones de solo lectura compartidas por las vistas de reportes CSV."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Iterable

from app.templates.manager import TemplateManager


# Con lo que abren las listas de ejecuciones, aquí y en «Indexar en
# AirVault». Es la misma lista en las dos ventanas y se elige igual, así que
# dice lo mismo con las mismas palabras.
TEXTO_ELEGIR_EJECUCION = "Seleccionar ejecución"

_CSV_METADATA_SUFFIXES = ("_conf", "_status", "_comment", "_source")
_ALWAYS_IMPORTANT_COLUMNS = frozenset(
    {"file", "page", "dup", "disc", "date", "time_ms"}
)
_DATE_COMPONENT_FIELDS = frozenset({"day", "month", "year"})
_FALLBACK_IMPORTANT_FIELDS = frozenset(
    {"log_number", "matricula", "flight_number", "captain_license"}
)


def csv_field_id(column: str, columns: Iterable[str]) -> str | None:
    """Devuelve el campo lógico al que pertenece una columna CSV."""
    column_set = set(columns)
    if column in _ALWAYS_IMPORTANT_COLUMNS:
        return None
    for suffix in _CSV_METADATA_SUFFIXES:
        if column.endswith(suffix):
            candidate = column[: -len(suffix)]
            if candidate in column_set:
                return candidate
    return column


def infer_important_field_ids(columns: Iterable[str]) -> set[str]:
    """Infiere campos principales cuando la plantilla original no está disponible."""
    column_list = list(columns)
    value_columns = {
        column
        for column in column_list
        if column not in _ALWAYS_IMPORTANT_COLUMNS
        and not any(column.endswith(suffix) for suffix in _CSV_METADATA_SUFFIXES)
    }
    important = value_columns & _FALLBACK_IMPORTANT_FIELDS
    important.update(
        column for column in value_columns if column.endswith("_signature")
    )
    if "date" not in column_list:
        important.update(value_columns & _DATE_COMPONENT_FIELDS)
    return important


def template_field_ids_for_columns(
    selected_columns: Iterable[str],
    template_field_ids: Iterable[str],
    columns: Iterable[str] = (),
) -> set[str]:
    """Traduce columnas marcadas a los campos de plantilla que las producen.

    El selector trabaja con columnas del CSV y el visor dibuja campos de la
    plantilla. Las columnas de metadatos (``_conf``, ``_status``…) apuntan al
    mismo campo que su valor, y la columna consolidada ``date`` representa
    las casillas día/mes/año.
    """
    selected = list(selected_columns)
    column_list = list(columns) or selected
    available = set(template_field_ids)
    resolved: set[str] = set()
    for column in selected:
        if column == "date":
            resolved.update(available & _DATE_COMPONENT_FIELDS)
            continue
        field_id = csv_field_id(column, column_list)
        if field_id in available:
            resolved.add(field_id)
    return resolved


def important_csv_columns(
    columns: Iterable[str], important_field_ids: Iterable[str] = ()
) -> list[str]:
    """Columnas de la vista resumida; nunca altera el CSV original.

    La vista resumida muestra los identificadores de fila, los valores de los
    campos obligatorios y la fecha/tiempo consolidados. Confianza, estado,
    comentario y fuente permanecen disponibles al activar la vista completa.
    """
    column_list = list(columns)
    important = set(important_field_ids) or infer_important_field_ids(column_list)
    if "date" in column_list:
        important.difference_update(_DATE_COMPONENT_FIELDS)
    return [
        column
        for column in column_list
        if column in _ALWAYS_IMPORTANT_COLUMNS or column in important
    ]


def find_csv_files(folder: Path) -> list[Path]:
    """Encuentra reportes CSV en una carpeta de ejecución o carpeta contenedora."""
    folder = Path(folder)
    if not folder.is_dir():
        return []

    def csvs_in(root: Path, recursive: bool = False) -> list[Path]:
        if not root.is_dir():
            return []
        paths = root.rglob("*") if recursive else root.iterdir()
        return [
            path for path in paths
            if path.is_file() and path.suffix.lower() == ".csv"
        ]

    # La estructura actual guarda el reporte en <corrida>/datos/. Las dos
    # rutas siguientes cubren además las ejecuciones históricas con CSV en raíz.
    preferred = csvs_in(folder / "datos")
    direct = csvs_in(folder)
    found = preferred + [path for path in direct if path not in preferred]
    if not found:
        found = csvs_in(folder, recursive=True)
    return sorted(set(found), key=lambda path: str(path).casefold())


def _holds_csv(folder: Path) -> bool:
    """¿Hay algún CSV suelto en esta carpeta? Sin entrar en las de dentro."""
    try:
        return any(
            path.is_file() and path.suffix.lower() == ".csv"
            for path in Path(folder).iterdir()
        )
    except OSError:
        return False


def find_run_dirs(root: Path, limit: int | None = None) -> list[Path]:
    """Ejecuciones guardadas en ``root``, de la más reciente a la más antigua.

    Una ejecución es una carpeta con su reporte dentro: en ``datos/`` las
    actuales y en la propia carpeta las históricas. Lo demás que vive junto a
    ellas (los logs, los recortes de firmas) no es una ejecución y no aparece.
    El recorrido se queda en el primer nivel de cada carpeta: recorrerlas
    enteras solo para saber si son ejecuciones costaría más que abrir la que se
    elija.
    """
    root = Path(root)
    if not root.is_dir():
        return []
    runs = [
        path
        for path in root.iterdir()
        if path.is_dir() and (_holds_csv(path / "datos") or _holds_csv(path))
    ]

    def recency(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    runs.sort(key=lambda path: (recency(path), path.name.casefold()), reverse=True)
    return runs[:limit] if limit else runs


def read_csv_file(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """Lee un CSV generado por la aplicación sin escribir sobre él."""
    with Path(path).open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError("El CSV no contiene encabezados.")
        columns = list(reader.fieldnames)
        rows = [
            {column: (row.get(column) or "") for column in columns}
            for row in reader
        ]
    return columns, rows


def template_name_for_csv(path: Path) -> str | None:
    """Plantilla con la que se escribio ese CSV, segun su JSON companero.

    El CSV completo no tiene JSON propio: la ejecucion escribe uno solo,
    con el nombre del CSV minimo. Sin este respaldo, abrir el completo
    dejaba la ejecucion sin plantilla, y con ella se perdian los campos
    importantes que esa plantilla tiene guardados.
    """
    companion = path.with_suffix(".json")
    if not companion.is_file() and path.stem.casefold().endswith("_completo"):
        companion = path.with_name(
            f"{path.stem[: -len('_completo')]}.json"
        )
    if not companion.is_file():
        return None
    try:
        payload = json.loads(companion.read_text(encoding="utf-8"))
        reports = payload.get("reportes", [])
        if reports:
            return reports[0].get("template_name")
        return payload.get("template_name")
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def template_for_csv(path: Path):
    """Plantilla con la que se procesó el CSV, si sigue estando disponible.

    El CSV solo guarda el nombre de la plantilla, así que se busca por nombre
    entre las del programa. Sin ella no se pueden volver a generar las
    salidas de la ejecución ni saber qué campos declaró importantes.
    """
    template_name = template_name_for_csv(path)
    if not template_name:
        return None
    manager = TemplateManager()
    for template_path in manager.list_templates_with_fallback():
        try:
            template = manager.load(template_path)
        except Exception:  # noqa: BLE001 - una plantilla ajena no bloquea el visor
            continue
        if template.name == template_name:
            return template
    return None


def important_field_ids_for_csv(path: Path, columns: Iterable[str]) -> set[str]:
    """Recupera los campos obligatorios desde la plantilla de la ejecución."""
    template = template_for_csv(path)
    if template is not None:
        important = {field.id for field in template.fields if field.required}
        important.update(
            field.id
            for field in template.fields
            if field.type.value == "signature"
        )
        important.update(
            {"captain_license", "flight_number"}.intersection(columns)
        )
        return important
    return infer_important_field_ids(columns)

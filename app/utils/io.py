"""Helpers de entrada/salida de archivos."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, Optional


# Los archivos ya procesados salen de input/ y se guardan aquí, dentro de la
# misma carpeta: la entrada queda con lo que falta por procesar y lo anterior
# no se pierde ni se confunde con lo nuevo.
PROCESSED_DIRNAME = "processed"


def ensure_dir(path: Path) -> Path:
    """Crea un directorio si no existe y lo devuelve."""
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def sanitize_filename(name: str) -> str:
    """Reemplaza caracteres no seguros para nombres de archivo."""
    return re.sub(r'[<>:"/\\|?*]', "_", name).strip()


def unique_path(path: Path) -> Path:
    """Devuelve una ruta libre añadiendo ``-2``, ``-3``… al nombre.

    Al exportar de nuevo sobre una ejecución existente los archivos previos
    se conservan: si ``bitacoras.pdf`` ya está, la copia nueva se llama
    ``bitacoras-2.pdf``. Es la misma convención que usan las carpetas de
    ejecución cuando el nombre con fecha y hora ya existe.
    """
    path = Path(path)
    if not path.exists():
        return path
    n = 2
    while True:
        candidate = path.with_name(f"{path.stem}-{n}{path.suffix}")
        if not candidate.exists():
            return candidate
        n += 1


def output_paths(
    output_dir: Path, base_name: str
) -> tuple[Path, Path]:
    """Devuelve las rutas de salida (json, csv) para un PDF."""
    output_dir = ensure_dir(output_dir)
    safe = sanitize_filename(base_name)
    return (
        output_dir / f"resultado_{safe}.json",
        output_dir / f"resultado_{safe}.csv",
    )


def resolve_processed_path(path: Path) -> Path:
    """Encuentra el PDF ya sea en ``input/`` o en ``input/processed``.

    Los reportes y la vista previa guardan la ruta del PDF tal como estaba
    cuando se leyó, pero ``archive_processed_files`` puede moverlo a
    ``processed`` después. Quien solo tenga esa ruta guardada (por ejemplo
    una celda de la tabla ya pintada) la sigue encontrando sin importar en
    cuál de las dos carpetas quedó.
    """
    path = Path(path)
    if path.exists():
        return path
    if path.parent.name == PROCESSED_DIRNAME:
        candidate = path.parent.parent / path.name
    else:
        candidate = path.parent / PROCESSED_DIRNAME / path.name
    return candidate if candidate.exists() else path


def archive_processed_files(
    paths: Iterable[Path], input_dir: Path
) -> tuple[dict[Path, Path], list[tuple[Path, Exception]]]:
    """Aparta a ``input/processed`` los archivos que ya se procesaron.

    Solo se mueve lo que está suelto en ``input_dir``: los PDF que el usuario
    eligió de sus propias carpetas se quedan donde están, porque esa carpeta
    no es del programa. El nombre repetido no pisa al anterior, se numera
    igual que las salidas (``bitacora-2.pdf``).

    Devuelve a dónde fue a parar cada archivo movido y los que no se pudieron
    mover, para que quien llame pueda seguir apuntando al sitio correcto.
    """
    import shutil

    input_dir = Path(input_dir)
    archive = input_dir / PROCESSED_DIRNAME
    pending: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        path = Path(path)
        try:
            resolved = path.resolve()
        except OSError:
            continue
        key = str(resolved).casefold()
        if key in seen or not resolved.is_file():
            continue
        # Solo la carpeta input/ en sí: lo que ya está apartado no se vuelve
        # a mover, y un PDF de otra carpeta del usuario no se toca.
        if str(resolved.parent).casefold() != str(input_dir.resolve()).casefold():
            continue
        seen.add(key)
        pending.append(resolved)

    moved: dict[Path, Path] = {}
    failed: list[tuple[Path, Exception]] = []
    if not pending:
        return moved, failed
    try:
        ensure_dir(archive)
    except OSError as exc:
        return moved, [(path, exc) for path in pending]
    for path in pending:
        destination = unique_path(archive / path.name)
        try:
            shutil.move(str(path), str(destination))
        except Exception as exc:  # noqa: BLE001 - se informa por archivo
            failed.append((path, exc))
            continue
        moved[path] = destination
    return moved, failed


def send_to_trash(
    paths: list[Path],
) -> tuple[list[Path], list[tuple[Path, Exception]]]:
    """Mueve archivos a la Papelera de reciclaje sin borrarlos definitivamente."""
    from send2trash import send2trash

    moved: list[Path] = []
    failed: list[tuple[Path, Exception]] = []
    for path in paths:
        path = Path(path)
        try:
            send2trash(str(path))
            moved.append(path)
        except Exception as exc:  # noqa: BLE001 - se informa por archivo
            failed.append((path, exc))
    return moved, failed

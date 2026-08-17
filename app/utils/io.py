"""Helpers de entrada/salida de archivos."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional


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

    Al exportar de nuevo sobre una corrida existente los archivos previos
    se conservan: si ``bitacoras.pdf`` ya está, la copia nueva se llama
    ``bitacoras-2.pdf``. Es la misma convención que usan las carpetas de
    corrida cuando el nombre con fecha y hora ya existe.
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


def resolve_tesseract_path() -> Optional[str]:
    """Localiza tesseract.exe (portable o en el sistema)."""
    import os
    import shutil

    if shutil.which("tesseract"):
        return "tesseract"

    candidates = [
        Path(__file__).resolve().parents[2] / "portable" / "tesseract",
        Path("portable/tesseract"),
    ]
    for base in candidates:
        exe = base / ("tesseract.exe" if os.name == "nt" else "tesseract")
        if exe.exists():
            return str(exe)
    return None

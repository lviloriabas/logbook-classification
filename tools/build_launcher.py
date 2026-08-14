#!/usr/bin/env python3
"""Construye el lanzador desde cualquier ubicación de la carpeta portable."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def build_command(root: Path, python_executable: str) -> list[str]:
    """Crea el comando usando únicamente la ubicación actual del proyecto."""
    root = Path(root).resolve()
    return [
        python_executable,
        "-m",
        "PyInstaller",
        "--clean",
        "--onefile",
        "--noconsole",
        "--name",
        "LogbookClassification",
        "--icon",
        str(root / "assets" / "icon.ico"),
        "--distpath",
        str(root),
        "--workpath",
        str(root / "build"),
        "--specpath",
        str(root / "build"),
        str(root / "launcher_gui.py"),
    ]


def main() -> int:
    root = ROOT.resolve()
    icon = root / "assets" / "icon.ico"
    if not icon.is_file():
        print(f"No se encontró el icono: {icon}", file=sys.stderr)
        return 1

    command = build_command(root, sys.executable)
    try:
        subprocess.run(command, cwd=root, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"No se pudo construir el lanzador: {exc}", file=sys.stderr)
        return 1
    print(f"Lanzador generado: {root / 'LogbookClassification.exe'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

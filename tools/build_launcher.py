#!/usr/bin/env python3
"""Construye el lanzador con rutas absolutas para conservar su icono."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    icon = (ROOT / "assets" / "icon.ico").resolve()
    launcher = (ROOT / "launcher_gui.py").resolve()
    if not icon.is_file():
        print(f"No se encontró el icono: {icon}", file=sys.stderr)
        return 1

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--clean",
        "--onefile",
        "--noconsole",
        "--name",
        "LogbookClassification",
        "--icon",
        str(icon),
        "--distpath",
        str(ROOT),
        "--workpath",
        str(ROOT / "build"),
        "--specpath",
        str(ROOT / "build"),
        str(launcher),
    ]
    try:
        subprocess.run(command, cwd=ROOT, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"No se pudo construir el lanzador: {exc}", file=sys.stderr)
        return 1
    print(f"Lanzador generado: {ROOT / 'LogbookClassification.exe'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

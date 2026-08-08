#!/usr/bin/env python3
"""Punto de entrada de la GUI principal de Logbook Classification."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT))

from app.utils.portable import ensure_portable_env

ensure_portable_env()
os.chdir(_ROOT)

from app.utils.logging import setup_logging


def main() -> int:
    setup_logging(Path("output") / "logs")
    try:
        from PySide6.QtWidgets import QApplication
        from app.gui.main_window import MainWindow
    except ImportError as exc:
        print("PySide6 no está instalado. Ejecute:\n"
              "  python -m pip install --user -r requirements.txt",
              file=sys.stderr)
        return 1

    from PySide6.QtGui import QIcon

    root = Path(__file__).resolve().parent
    icon = root / "assets" / "icon.png"
    app = QApplication(sys.argv)
    app.setApplicationName("Logbook Classification")
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Punto de entrada del editor visual de plantillas."""

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
from app.utils.app_identity import set_windows_taskbar_icon


def main() -> int:
    setup_logging(Path("output") / "logs")
    try:
        from PySide6.QtWidgets import QApplication
        from app.gui.editor_window import EditorWindow
    except ImportError as exc:
        print("PySide6 no está instalado. Ejecute:\n"
              "  python -m pip install --user -r requirements.txt",
              file=sys.stderr)
        return 1

    app = QApplication(sys.argv)
    app.setApplicationName("Logbook Classification - Editor de Plantillas")
    app.setApplicationDisplayName(
        "Logbook Classification - Editor de Plantillas"
    )
    app.setOrganizationName("BITS")
    root = Path(__file__).resolve().parent
    icon = root / "assets" / "icon.ico"
    if not icon.exists():
        icon = root / "assets" / "icon.png"
    from PySide6.QtGui import QIcon

    app_icon = QIcon(str(icon))
    app.setWindowIcon(app_icon)
    window = EditorWindow()
    window.setWindowIcon(app_icon)
    window.show()
    set_windows_taskbar_icon(window, icon)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

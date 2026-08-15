#!/usr/bin/env python3
"""Punto de entrada de la GUI principal de Logbook Classification."""

from __future__ import annotations

import os
import sys
from functools import partial
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
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication
        from app.gui.main_window import MainWindow
    except ImportError as exc:
        print("PySide6 no está instalado. Ejecute:\n"
              "  python -m pip install --user -r requirements.txt",
              file=sys.stderr)
        return 1

    from PySide6.QtGui import QIcon

    root = Path(__file__).resolve().parent
    icon = root / "assets" / "icon.ico"
    if not icon.exists():
        icon = root / "assets" / "icon.png"
    app = QApplication(sys.argv)
    app.setApplicationName("Logbook Classification")
    app.setApplicationDisplayName("Logbook Classification")
    app.setOrganizationName("BITS")
    app_icon = QIcon(str(icon))
    app.setWindowIcon(app_icon)
    window = MainWindow()
    window.setWindowIcon(app_icon)
    window.show()
    set_windows_taskbar_icon(window, icon)
    # Qt y el shell terminan de crear/asociar el botón de la barra de tareas
    # después de show(). Se reafirma entonces el icono de la ventana real.
    QTimer.singleShot(
        250,
        partial(set_windows_taskbar_icon, window, icon),
    )
    # Detectar PDFs y calcular la estimación puede tocar archivos grandes.
    # Se difiere para que la ventana sea visible inmediatamente.
    QTimer.singleShot(0, window.load_initial_data)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

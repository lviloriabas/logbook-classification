"""Identidad estable de la aplicación para el shell de Windows."""

from __future__ import annotations

import sys


APP_USER_MODEL_ID = "BITS.LogbookClassification"


def set_windows_app_user_model_id(
    app_id: str = APP_USER_MODEL_ID,
) -> bool:
    """Asocia el proceso actual con el ejecutable BITS en la barra de tareas.

    La distribución usa un ``.exe`` lanzador que inicia ``pythonw.exe``. Sin
    un AppUserModelID explícito, Windows puede identificar la ventana como
    Python y sustituir su icono por el genérico. Debe llamarse antes de crear
    ``QApplication``.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        result = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            app_id
        )
    except (AttributeError, OSError):
        return False
    return result == 0

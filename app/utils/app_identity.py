"""Integración de la ventana de la aplicación con el shell de Windows."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any


WM_SETICON = 0x0080
ICON_SMALL = 0
ICON_BIG = 1
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
SM_CXICON = 11
SM_CYICON = 12
SM_CXSMICON = 49
SM_CYSMICON = 50
GCLP_HICON = -14
GCLP_HICONSM = -34


def set_windows_taskbar_icon(
    window: Any,
    icon_path: Path | str,
) -> bool:
    """Instala el ICO en la ventana nativa que consulta la barra de tareas.

    ``QApplication.setWindowIcon`` sigue siendo el mecanismo portable. Este
    refuerzo se ejecuta después de ``show()`` y envía ``WM_SETICON`` con ambos
    tamaños al HWND real. No se fija un AppUserModelID personalizado: Windows
    exige registrar un acceso directo para esos IDs y, si no existe, puede
    sustituir el icono de la ventana por uno genérico.
    """
    path = Path(icon_path)
    if sys.platform != "win32" or path.suffix.lower() != ".ico":
        return False
    if not path.is_file():
        return False
    try:
        import ctypes

        user32 = ctypes.windll.user32
        load_image = user32.LoadImageW
        load_image.restype = ctypes.c_void_p
        hwnd = int(window.winId())

        def load(metric_x: int, metric_y: int) -> int:
            width = user32.GetSystemMetrics(metric_x)
            height = user32.GetSystemMetrics(metric_y)
            return int(load_image(
                None,
                str(path.resolve()),
                IMAGE_ICON,
                width,
                height,
                LR_LOADFROMFILE,
            ) or 0)

        big = load(SM_CXICON, SM_CYICON)
        small = load(SM_CXSMICON, SM_CYSMICON)
        if not big or not small:
            return False
        user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, big)
        user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, small)
        # Windows 11 puede consultar el icono de la clase en lugar de enviar
        # WM_GETICON cuando crea o reagrupa el botón de la barra de tareas.
        # Se actualizan ambos orígenes con los mismos handles.
        set_class_icon = user32.SetClassLongPtrW
        set_class_icon.restype = ctypes.c_void_p
        set_class_icon(hwnd, GCLP_HICON, big)
        set_class_icon(hwnd, GCLP_HICONSM, small)
        # Los HICON deben permanecer válidos durante toda la vida del HWND.
        # El proceso los libera al cerrarse; dos handles por ventana son
        # preferibles a destruirlos mientras Windows aún puede consultarlos.
        window._bits_native_icon_handles = (big, small)
    except (AttributeError, OSError, TypeError, ValueError):
        return False
    return True

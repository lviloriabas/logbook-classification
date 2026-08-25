"""Impide que los subprocesos de terceros abran ventanas de consola.

La aplicación se ejecuta sin consola (``pythonw.exe`` desde el lanzador). En
Windows, cuando un proceso sin consola lanza un ejecutable de consola, el
sistema le **crea una consola nueva**: una ventana negra que aparece y
desaparece. No hace falta que el programa quiera una terminal; basta con que
una dependencia llame a ``subprocess`` sin decir lo contrario.

Eso es justo lo que hace PaddlePaddle: al importar ``paddle`` se ejecuta
``paddle/utils/cpp_extension/cpp_extension.py``, que busca herramientas de
compilación con ``where nvcc`` y ``where ccache``. Son dos ventanas por cada
proceso que importa el motor OCR, así que al repartir el OCR en un proceso
por núcleo el parpadeo se multiplicó por el número de procesos.

No se puede arreglar en el origen (es código de la dependencia, y la carpeta
portable debe poder reinstalarse), así que se corrige en el único punto que
los cubre a todos: ``subprocess.Popen``. Los subprocesos propios que ya
deciden su consola (``llama-server`` con ``CREATE_NO_WINDOW``, Tesseract con
``STARTUPINFO``) se dejan intactos.
"""

from __future__ import annotations

import subprocess
import sys

# Ejecuta el hijo sin consola alguna.
CREATE_NO_WINDOW = 0x08000000
# Banderas con las que quien llama ya eligió qué consola quiere.
_EXPLICIT_CONSOLE = (
    0x00000010  # CREATE_NEW_CONSOLE
    | 0x00000008  # DETACHED_PROCESS
    | CREATE_NO_WINDOW
)
# Posición de ``creationflags`` en la firma de Popen, sin contar ``self``.
_CREATIONFLAGS_POSITION = 13

_installed = False


def suppress_child_consoles() -> bool:
    """Añade ``CREATE_NO_WINDOW`` a los ``Popen`` que no eligen consola.

    Es idempotente y solo actúa en Windows. Devuelve True si dejó el parche
    instalado (en otras plataformas no hay nada que hacer).
    """
    global _installed
    if sys.platform != "win32":
        return False
    if _installed:
        return True

    original_init = subprocess.Popen.__init__

    def patched_init(self, *args, **kwargs):
        # Con ``creationflags`` posicional, quien llama ya decidió: no se toca.
        if len(args) <= _CREATIONFLAGS_POSITION:
            flags = kwargs.get("creationflags", 0)
            if not flags & _EXPLICIT_CONSOLE:
                kwargs["creationflags"] = flags | CREATE_NO_WINDOW
        original_init(self, *args, **kwargs)

    subprocess.Popen.__init__ = patched_init
    _installed = True
    return True

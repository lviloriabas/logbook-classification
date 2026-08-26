"""La aplicación no puede abrir ventanas de consola al procesar.

La GUI corre sin consola, así que Windows le crea una ventana nueva a cada
subproceso de consola que se lance sin decirle lo contrario. Al importar
``paddle`` se ejecutan ``where nvcc`` y ``where ccache``, dos ventanas por
cada proceso OCR; con un proceso por núcleo eso llenaba la pantalla de
terminales parpadeando.
"""

from __future__ import annotations

import ssl
import subprocess
import sys
from types import SimpleNamespace

import pytest
import truststore

from app.utils.no_console import CREATE_NO_WINDOW, suppress_child_consoles

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="El parche solo aplica a Windows"
)


@pytest.fixture
def popen_calls(monkeypatch):
    """Captura los argumentos con los que se construiría el proceso real.

    Deja el parche sin instalar para que cada prueba elija por dónde lo
    instala, y restaura ``Popen`` al terminar.
    """
    calls: list[dict] = []

    def fake_init(self, *args, **kwargs):
        calls.append(kwargs)
        # No se crea proceso alguno: solo interesa la bandera resultante.
        # Los atributos son los que Popen.__del__ espera encontrar.
        self._child_created = False
        self.returncode = 0

    monkeypatch.setattr(subprocess.Popen, "__init__", fake_init)
    monkeypatch.setattr("app.utils.no_console._installed", False)
    return calls


def test_plain_subprocess_gets_no_console(popen_calls):
    suppress_child_consoles()
    subprocess.Popen(["where", "nvcc"])
    assert popen_calls[0]["creationflags"] & CREATE_NO_WINDOW


def test_without_the_patch_nothing_is_added(popen_calls):
    """Confirma que la prueba anterior mide el parche y no un valor por defecto."""
    subprocess.Popen(["where", "nvcc"])
    assert "creationflags" not in popen_calls[0]


def test_other_creation_flags_are_preserved(popen_calls):
    suppress_child_consoles()
    idle_priority = 0x00000040
    subprocess.Popen(["where", "ccache"], creationflags=idle_priority)
    flags = popen_calls[0]["creationflags"]
    assert flags & CREATE_NO_WINDOW
    assert flags & idle_priority


def test_an_explicit_console_choice_is_respected(popen_calls):
    """Un subproceso que ya elige su consola no se toca."""
    suppress_child_consoles()
    detached_process = 0x00000008
    subprocess.Popen(["cmd"], creationflags=detached_process)
    assert popen_calls[0]["creationflags"] == detached_process


def test_installing_twice_does_not_stack_wrappers(popen_calls):
    suppress_child_consoles()
    suppress_child_consoles()
    subprocess.Popen(["where", "nvcc"])
    assert popen_calls[0]["creationflags"] == CREATE_NO_WINDOW


def test_certificados_de_windows_se_instalan_una_sola_vez(monkeypatch):
    from app.utils import portable

    llamadas = []
    falso = SimpleNamespace(inject_into_ssl=lambda: llamadas.append(True))
    monkeypatch.setitem(sys.modules, "truststore", falso)
    monkeypatch.setattr(portable, "_TLS_DEL_SISTEMA_ACTIVO", False)

    assert portable.usar_certificados_de_windows() is True
    assert portable.usar_certificados_de_windows() is True
    assert llamadas == [True]


def test_portable_environment_installs_the_patch(popen_calls):
    """El motor OCR llama a ``ensure_portable_env`` antes de importar paddle.

    Ese es el punto por el que pasan todos los procesos (la GUI, el CLI y
    cada worker del pool), así que basta con instalarlo ahí.
    """
    from app.utils.portable import ensure_portable_env

    ensure_portable_env()
    subprocess.Popen(["where", "nvcc"])

    assert popen_calls[0]["creationflags"] & CREATE_NO_WINDOW
    assert ssl.SSLContext is truststore.SSLContext

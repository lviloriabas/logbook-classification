"""Lanzador del servidor llama.cpp (``llama-server``) para el verificador VLM.

El binario y los modelos viven en ``portable/llama/`` (mismo patrón que
tesseract/paddlex):

- Binario: ``portable/llama/bin/llama-server.exe`` (o ``llama-server``
  en el PATH).
- Modelo de texto: ``portable/llama/models/*.gguf`` (sin ``mmproj``).
- Proyector multimodal: ``portable/llama/models/*mmproj*.gguf``.

Se pueden sobreescribir las rutas con las variables de entorno
``BITS_LLAMA_BIN``, ``BITS_LLAMA_MODEL`` y ``BITS_LLAMA_MMPROJ``.

El servidor es un subproceso local en ``127.0.0.1`` con un puerto
efímero: se levanta una sola vez por proceso y se apaga al salir.
Cualquier fallo de arranque desactiva el verificador sin romper el
pipeline (que queda idéntico al de antes).
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from loguru import logger

from app.utils.portable import app_root


@dataclass
class VlmPaths:
    """Binario y modelos resueltos, o todos None si faltan."""

    binary: Optional[str] = None
    model: Optional[Path] = None
    mmproj: Optional[Path] = None

    @property
    def complete(self) -> bool:
        return bool(self.binary and self.model and self.mmproj)


def _models_dir() -> Path:
    return app_root() / "portable" / "llama" / "models"


def _pick_model() -> Optional[Path]:
    """Modelo GGUF de texto: el primer ``*.gguf`` que no sea proyector."""
    folder = _models_dir()
    if not folder.is_dir():
        return None
    for path in sorted(folder.glob("*.gguf")):
        if "mmproj" not in path.name.lower():
            return path
    return None


def _pick_mmproj() -> Optional[Path]:
    folder = _models_dir()
    if not folder.is_dir():
        return None
    for path in sorted(folder.glob("*.gguf")):
        if "mmproj" in path.name.lower():
            return path
    return None


def resolve_paths() -> VlmPaths:
    """Localiza binario y modelos (env vars primero, luego portable/PATH)."""
    binary = os.environ.get("BITS_LLAMA_BIN")
    if not binary:
        base = app_root() / "portable" / "llama" / "bin"
        exe = base / ("llama-server.exe" if os.name == "nt" else "llama-server")
        if exe.exists():
            binary = str(exe)
        else:
            binary = shutil.which("llama-server")
    model: Optional[Path] = None
    mmproj: Optional[Path] = None
    if os.environ.get("BITS_LLAMA_MODEL"):
        model = Path(os.environ["BITS_LLAMA_MODEL"])
    else:
        model = _pick_model()
    if os.environ.get("BITS_LLAMA_MMPROJ"):
        mmproj = Path(os.environ["BITS_LLAMA_MMPROJ"])
    else:
        mmproj = _pick_mmproj()
    return VlmPaths(binary=binary, model=model, mmproj=mmproj)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class LlamaServer:
    """Subproceso ``llama-server`` con API OpenAI-compatible en 127.0.0.1.

    Usage::

        server = LlamaServer(threads=None)
        if server.start():
            ... server.base_url ...
        server.stop()
    """

    def __init__(self, threads: Optional[int] = None) -> None:
        self.threads = threads
        self._proc: Optional[subprocess.Popen] = None
        self.base_url: Optional[str] = None
        self.ready = False

    def start(self, timeout_s: float = 180.0) -> bool:
        """Arranca el servidor y espera su health check.

        Returns:
            True si quedó listo; en cualquier fallo False (nunca lanza).
        """
        paths = resolve_paths()
        if not paths.complete:
            logger.info(
                "[VLM] Sin llama-server o modelos GGUF "
                "(portable/llama/); verificador desactivado"
            )
            return False
        port = _free_port()
        args = [
            paths.binary,
            "-m", str(paths.model),
            "--mmproj", str(paths.mmproj),
            "--host", "127.0.0.1",
            "--port", str(port),
            "--ctx-size", "4096",
        ]
        if self.threads:
            args += ["--threads", str(self.threads)]
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self._proc = subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
        except Exception as exc:  # noqa: BLE001 - el verificador es opcional
            logger.warning(f"[VLM] No se pudo lanzar llama-server: {exc}")
            return False

        base_url = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                logger.warning("[VLM] llama-server terminó al iniciar")
                self.stop()
                return False
            if self._health(base_url):
                self.base_url = base_url
                self.ready = True
                logger.info(f"[VLM] Servidor listo en {base_url}")
                return True
            time.sleep(0.5)
        logger.warning("[VLM] Tiempo de espera agotado para llama-server")
        self.stop()
        return False

    @staticmethod
    def _health(base_url: str) -> bool:
        try:
            with urllib.request.urlopen(
                f"{base_url}/health", timeout=2.0
            ) as resp:
                return resp.status == 200 and b"ok" in resp.read().lower()
        except Exception:  # noqa: BLE001 - servidor aún arrancando
            return False

    def stop(self) -> None:
        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=10)
            except Exception:  # noqa: BLE001 - limpieza best-effort
                try:
                    self._proc.kill()
                except Exception:  # noqa: BLE001
                    pass
            self._proc = None
        self.ready = False
        self.base_url = None

    def __enter__(self) -> "LlamaServer":
        return self

    def __exit__(self, *exc) -> None:
        self.stop()
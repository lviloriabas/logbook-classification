"""Lanzador del servidor llama.cpp (``llama-server``) para el verificador VLM.

El binario y los modelos viven en ``portable/llama/`` (mismo patrón que
tesseract/paddlex):

- Binario: ``portable/llama/bin/llama-server.exe`` (o ``llama-server``
  en el PATH).
- Modelo de texto: ``portable/llama/models/*.gguf`` (sin ``mmproj``).
- Proyector multimodal: ``portable/llama/models/*mmproj*.gguf``.

Se pueden sobreescribir las rutas con las variables de entorno
``BITS_LLAMA_BIN``, ``BITS_LLAMA_MODEL`` y ``BITS_LLAMA_MMPROJ``, o con las
rutas explícitas de ``AppConfig`` para comparar, por ejemplo, SmolVLM2 y
Qwen3-VL sin depender del orden de los archivos.

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
        return bool(
            self.binary
            and self.model
            and self.mmproj
            and Path(self.binary).is_file()
            and self.model.is_file()
            and self.mmproj.is_file()
        )


def _models_dir() -> Path:
    return app_root() / "portable" / "llama" / "models"


def _pick_model() -> Optional[Path]:
    """Elige el modelo por defecto sin depender del orden de instalación.

    Qwen3-VL-8B-Instruct es el modelo por defecto. Si no está instalado,
    se usa SmolVLM2 como respaldo y luego el primer modelo disponible; otro
    modelo se selecciona de forma inequívoca mediante ``vlm_model`` o
    ``BITS_LLAMA_MODEL``.
    """
    folder = _models_dir()
    if not folder.is_dir():
        return None
    candidates = [
        path for path in sorted(folder.glob("*.gguf"))
        if "mmproj" not in path.name.lower()
    ]
    preferred = [
        path for path in candidates
        if "qwen3-vl" in path.name.lower()
    ]
    if preferred:
        return preferred[0]
    preferred = [
        path for path in candidates
        if "smolvlm" in path.name.lower()
    ]
    if preferred:
        return preferred[0]
    if candidates:
        return candidates[0]
    return None


def _pick_mmproj(model: Optional[Path | str] = None) -> Optional[Path]:
    folder = _models_dir()
    if not folder.is_dir():
        return None
    candidates = [
        path for path in sorted(folder.glob("*.gguf"))
        if "mmproj" in path.name.lower()
    ]
    model_name = Path(model).name.lower() if model is not None else ""
    if "qwen" in model_name or not model_name:
        # Qwen's standard projector names are generic (mmproj-F16/F32), so
        # match every non-Smol projector instead of requiring "qwen" in the
        # filename.
        preferred = [
            path for path in candidates
            if "smolvlm" not in path.name.lower()
        ]
    else:
        preferred = [
            path for path in candidates
            if "smolvlm" in path.name.lower()
        ]
    if preferred:
        return preferred[0]
    if candidates:
        return candidates[0]
    return None


def resolve_paths(
    model: Optional[Path | str] = None,
    mmproj: Optional[Path | str] = None,
) -> VlmPaths:
    """Localiza binario y modelos.

    Las variables de entorno conservan prioridad para permitir configurar el
    paquete portable sin tocar la aplicación. Los argumentos explícitos se
    usan antes de la autodetección y permiten comparar dos modelos instalados
    en la misma carpeta sin depender del orden alfabético de los GGUF.
    """
    binary = os.environ.get("BITS_LLAMA_BIN")
    if not binary:
        base = app_root() / "portable" / "llama" / "bin"
        exe = base / ("llama-server.exe" if os.name == "nt" else "llama-server")
        if exe.exists():
            binary = str(exe)
        else:
            binary = shutil.which("llama-server")
    if os.environ.get("BITS_LLAMA_MODEL"):
        model_path = Path(os.environ["BITS_LLAMA_MODEL"])
    elif model is not None:
        model_path = Path(model)
    else:
        model_path = _pick_model()
    if os.environ.get("BITS_LLAMA_MMPROJ"):
        mmproj_path = Path(os.environ["BITS_LLAMA_MMPROJ"])
    elif mmproj is not None:
        mmproj_path = Path(mmproj)
    else:
        mmproj_path = _pick_mmproj(model_path)
    return VlmPaths(binary=binary, model=model_path, mmproj=mmproj_path)


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

    def __init__(
        self,
        threads: Optional[int] = None,
        paths: Optional[VlmPaths] = None,
    ) -> None:
        self.threads = threads
        self.paths = paths
        self._proc: Optional[subprocess.Popen] = None
        self.base_url: Optional[str] = None
        self.ready = False

    def start(self, timeout_s: float = 180.0) -> bool:
        """Arranca el servidor y espera su health check.

        Returns:
            True si quedó listo; en cualquier fallo False (nunca lanza).
        """
        paths = self.paths or resolve_paths()
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

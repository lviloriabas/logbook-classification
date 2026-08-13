#!/usr/bin/env python3
"""Precarga los archivos del verificador VLM en la carpeta portable.

Ejecutar UNA VEZ en una máquina con internet para dejar descargado:
- ``portable/llama/models/mmproj-*.gguf``: proyector multimodal.
- ``portable/llama/models/*.gguf``: modelo de texto (Qwen3-VL por defecto).
- ``portable/llama/bin/llama-server(.exe)``: binario de llama.cpp.

Después, el paquete completo funciona sin conexión: el pipeline lanza el
binario localmente (127.0.0.1) y si no lo encuentra, actúa como siempre.

    portable\\python312\\tools\\python.exe tools\\precache_vlm.py

El preset por defecto descarga una cuantización Q4_K_M de Qwen3-VL-8B-Instruct
desde su repositorio GGUF. También se puede usar ``--preset smolvlm2`` para
descargar SmolVLM2. El binario NO se descarga por defecto: la URL de cada
release de llama.cpp cambia con el número de build; fíjela con la variable
``BITS_LLAMA_BIN_ZIP`` (URL del .zip win-x64) o descargue la carpeta
manualmente y este script solo validará.

Variables aceptadas (todas opcionales):
    BITS_LLAMA_MODEL_URL     URL del GGUF de texto.
    BITS_LLAMA_MMPROJ_URL  URL del GGUF del proyector.
    BITS_LLAMA_BIN_ZIP     URL del zip de llama.cpp (win-x64) con llama-server.

El verificador resuelve automáticamente las rutas; ver las variables
``BITS_LLAMA_BIN/MODEL/MMPROJ`` en app/verifier/launcher.py.
"""

from __future__ import annotations

import argparse
import io
import os
import sys
import urllib.request
import zipfile
from pathlib import Path

_DEFAULT_MODEL_URL = (
    "https://huggingface.co/HuggingFaceTB/SmolVLM2-500M-Instruct-gguf"
    "/resolve/main/SmolVLM2-500M-Instruct-Q4_K_M.gguf"
)
_DEFAULT_MMPROJ_URL = (
    "https://huggingface.co/HuggingFaceTB/SmolVLM2-500M-Instruct-gguf"
    "/resolve/main/mmproj-SmolVLM2-500M-Instruct-f16.gguf"
)
_QWEN_MODEL_URL = (
    "https://huggingface.co/unsloth/Qwen3-VL-8B-Instruct-GGUF"
    "/resolve/main/Qwen3-VL-8B-Instruct-Q4_K_M.gguf"
)
_QWEN_MMPROJ_URL = (
    "https://huggingface.co/unsloth/Qwen3-VL-8B-Instruct-GGUF"
    "/resolve/main/mmproj-F16.gguf"
)
# Dejar vacío por defecto: la URL exacta de los releases de llama.cpp cambia
# con cada build y no debe adivinarse.
_DEFAULT_BIN_ZIP = os.environ.get("BITS_LLAMA_BIN_ZIP", "")


def _download(url: str, dest: Path) -> None:
    print(f"  ← {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "opencode"})
    with urllib.request.urlopen(req) as resp, open(dest, "wb") as fh:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            fh.write(chunk)
            done += len(chunk)
            if total:
                print(f"\r  {done * 100 // total:3d}% "
                      f"({done / 1e6:.1f} / {total / 1e6:.1f} MB)", end="")
    print()


def _install_binario(zip_url: str) -> bool:
    req = urllib.request.Request(zip_url, headers={"User-Agent": "opencode"})
    with urllib.request.urlopen(req) as resp:
        data = resp.read()
    bin_dir = Path(__file__).resolve().parents[1] / "portable" / "llama" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    extraido = False
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for name in zf.namelist():
            base = Path(name).name
            if base in ("llama-server.exe", "llama-server"):
                zf.extract(name, bin_dir)
                print(f"  extraído: {bin_dir / name}")
                extraido = True
    if not extraido:
        print("  ADVERTENCIA: no se encontró llama-server en el zip.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Descarga un modelo GGUF multimodal y su proyector para "
            "llama-server. Por defecto descarga Qwen3-VL-8B-Instruct."
        )
    )
    parser.add_argument(
        "--model-url",
        help="URL del GGUF de texto; también BITS_LLAMA_MODEL_URL.",
    )
    parser.add_argument(
        "--mmproj-url",
        help="URL del GGUF mmproj; también BITS_LLAMA_MMPROJ_URL.",
    )
    parser.add_argument(
        "--bin-url",
        help="URL del ZIP de llama.cpp; también BITS_LLAMA_BIN_ZIP.",
    )
    parser.add_argument(
        "--preset",
        choices=("smolvlm2", "qwen3-vl-8b-instruct"),
        default="qwen3-vl-8b-instruct",
        help="Modelo preconfigurado (default: qwen3-vl-8b-instruct). Las URLs explícitas "
             "tienen prioridad.",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    models = root / "portable" / "llama" / "models"
    models.mkdir(parents=True, exist_ok=True)

    preset_urls = {
        "smolvlm2": (_DEFAULT_MODEL_URL, _DEFAULT_MMPROJ_URL),
        "qwen3-vl-8b-instruct": (_QWEN_MODEL_URL, _QWEN_MMPROJ_URL),
    }
    preset_model_url, preset_mmproj_url = preset_urls[args.preset]
    model_url = (
        args.model_url
        or os.environ.get("BITS_LLAMA_MODEL_URL", preset_model_url)
    )
    mmproj_url = (
        args.mmproj_url
        or os.environ.get("BITS_LLAMA_MMPROJ_URL", preset_mmproj_url)
    )
    bin_url = (
        args.bin_url
        or os.environ.get("BITS_LLAMA_BIN_ZIP", _DEFAULT_BIN_ZIP)
    )

    print(f"Descargando modelos del verificador VLM ({args.preset})...")
    for url in (model_url, mmproj_url):
        name = url.rstrip("/").split("/")[-1]
        dest = models / name
        if not dest.exists():
            _download(url, dest)
        else:
            print(f"  ya existe: {dest}")

    if bin_url:
        print("Descargando llama-server...")
        try:
            _install_binario(bin_url)
        except Exception as exc:  # noqa: BLE001 - el ZIP puede no coincidir
            print(f"  AVISO: no se pudo extraer el binario: {exc}")
            print("  Descárguelo a mano y déjelo en portable/llama/bin/")
    else:
        print(
            "binario: no configurado → déjelo en portable/llama/bin/"
            " (o defina BITS_LLAMA_BIN_ZIP con la URL del release)."
        )

    print("Listo. La carpeta portable/llama/ queda:")
    for path in sorted((root / "portable" / "llama").rglob("*")):
        if path.is_file():
            print(f"  {path.relative_to(root)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

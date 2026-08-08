"""Helpers de portabilidad: mantienen TODO dentro de la carpeta del proyecto.

La aplicación debe funcionar copiando la carpeta completa a cualquier
máquina Windows, sin instalar nada ni tocar el perfil del usuario. Este
módulo redirige los caches que por defecto escriben fuera de la carpeta:
modelos PaddleOCR/paddlex, TESSDATA de Tesseract y variables de red.
"""

from __future__ import annotations

import os
from pathlib import Path


def app_root() -> Path:
    """Raíz del proyecto (BITS/)."""
    return Path(__file__).resolve().parents[2]


def ensure_portable_env() -> Path:
    """Fija el entorno portable y devuelve la raíz del proyecto.

    Debe llamarse lo antes posible en cada punto de entrada, ANTES de
    importar paddleocr/paddlex, porque esos módulos leen las variables
    al momento del import. Configura:

    - PADDLE_PDX_CACHE_HOME  → <raíz>/portable/paddlex
      (los modelos OCR se descargan/leen desde la carpeta del proyecto,
       nunca de ~/.paddlex del usuario).
    - PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=1
      (omite el chequeo de conectividad a los servidores de modelos).
    - PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT=0
      (evita el bug de oneDNN en Windows: ConvertPirAttribute2Runtime...).
    - TESSDATA_PREFIX → <raíz>/portable/tesseract/tessdata si existe.
    """
    root = app_root()
    os.environ.setdefault(
        "PADDLE_PDX_CACHE_HOME", str(root / "portable" / "paddlex")
    )
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "1")
    os.environ.setdefault("PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT", "0")
    os.environ.setdefault("FLAGS_use_mkldnn", "0")
    tessdata = root / "portable" / "tesseract" / "tessdata"
    if tessdata.is_dir():
        os.environ.setdefault("TESSDATA_PREFIX", str(tessdata))
    return root

#!/usr/bin/env python3
"""Precarga los modelos PaddleOCR en la caché portable (100% offline).

Ejecutar UNA VEZ en una máquina con internet para dejar descargados los
modelos en ``portable/paddlex/official_models``. Después, el paquete
completo funciona sin conexión ni instalación.

    portable\\python312\\tools\\python.exe tools\\precache_paddle.py

Los nombres de modelo son los usados por app/ocr/engine.py:
el detector ``PP-OCRv6_medium_det`` (mejor con manuscrito pequeño) +
el reconocedor ``PP-OCRv5_mobile_rec`` (mejor en escritura a mano).
Ambos son la configuración fija validada. No se precargan modelos alternativos
porque la aplicación no encadena motores ni cambia de modelo en ejecución.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from app.utils.portable import ensure_portable_env

ensure_portable_env()

import numpy as np  # noqa: E402

MODELS = [
    ("PP-OCRv6_medium_det", "PP-OCRv5_mobile_rec"),
]


def main() -> int:
    from paddleocr import PaddleOCR

    image = np.zeros((480, 640, 3), dtype=np.uint8)
    for det_model, rec_model in MODELS:
        name = f"{det_model} + {rec_model}"
        print(f"Descargando/inicializando {name} (puede tardar)...")
        engine = PaddleOCR(
            lang="en",
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            text_detection_model_name=det_model,
            text_recognition_model_name=rec_model,
        )
        engine.predict(image)
        print(f"  {name} listo.")
    print("Modelos listos. Caché en:", _ROOT / "portable" / "paddlex")
    return 0


if __name__ == "__main__":
    sys.exit(main())

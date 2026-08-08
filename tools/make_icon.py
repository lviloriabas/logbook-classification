#!/usr/bin/env python3
"""Genera los iconos de Logbook Classification.

Crea ``assets/icon.png`` (512) y ``assets/icon.ico`` (multi-tamaño) a
partir del diseño de ``assets/icon.svg``. Requiere Pillow, que ya está
incluido en el Python portable del programa.

Uso (una sola vez al reconstruir el paquete):
    portable\\python312\\tools\\python.exe tools\\make_icon.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"

PAPER = (255, 255, 255)      # #ffffff
OUTLINE = (184, 201, 224)    # #b8c9e0
FOLD = (211, 226, 245)       # #d3e2f5
LINE = (198, 215, 236)       # #c6d7ec


def draw_icon(size: int) -> Image.Image:
    """Dibuja el icono a un tamaño dado (solo el papel, sin fondo)."""
    s = size / 512.0
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Papel plano con contorno sutil.
    d.rounded_rectangle([64 * s, 56 * s, 448 * s, 456 * s],
                        radius=max(2, int(20 * s)),
                        fill=PAPER, outline=OUTLINE,
                        width=max(1, int(10 * s)))
    # Esquina doblada.
    d.polygon([(348 * s, 56 * s), (448 * s, 56 * s),
               (448 * s, 166 * s), (348 * s, 146 * s)], fill=FOLD)
    # Líneas de texto.
    for x, y, w in ((104, 160, 180), (104, 212, 280),
                    (104, 264, 240), (104, 316, 150)):
        d.rounded_rectangle([x * s, y * s, (x + w) * s, (y + 20) * s],
                            radius=max(1, int(10 * s)), fill=LINE)
    return img


def main() -> int:
    ASSETS.mkdir(parents=True, exist_ok=True)
    try:
        icon = draw_icon(512)
        png_path = ASSETS / "icon.png"
        icon.save(png_path)
        ico_path = ASSETS / "icon.ico"
        sizes = [(256, 256), (128, 128), (64, 64), (48, 48),
                 (32, 32), (24, 24), (16, 16)]
        icon.save(ico_path, format="ICO", sizes=sizes)
        print(f"Iconos generados:\n  {png_path}\n  {ico_path}")
        return 0
    except Exception as exc:  # noqa: BLE001 - error de generación
        print(f"ERROR generando iconos: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
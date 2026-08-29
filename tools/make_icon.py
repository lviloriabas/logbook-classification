#!/usr/bin/env python3
"""Genera los iconos de BITS - Clasificación de Bitácoras.

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

BACKGROUND = (22, 52, 93)    # #16345d
PAPER = (255, 255, 255)      # #ffffff
FOLD = (102, 185, 242)       # #66b9f2
LINE = (138, 168, 199)       # #8aa8c7
CHECK = (45, 190, 115)       # #2dbe73


def draw_icon(size: int) -> Image.Image:
    """Dibuja una página horizontal legible incluso a 16 px."""
    s = size / 512.0
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Fondo oscuro: evita que la hoja desaparezca sobre la taskbar clara.
    d.rounded_rectangle([20 * s, 20 * s, 492 * s, 492 * s],
                        radius=max(2, int(92 * s)), fill=BACKGROUND)
    # Hoja apaisada: recuerda la página física de una bitácora abierta.
    d.rounded_rectangle([65 * s, 115 * s, 447 * s, 397 * s],
                        radius=max(2, int(28 * s)), fill=PAPER)
    # Esquina doblada.
    d.polygon([(347 * s, 115 * s), (447 * s, 115 * s),
               (447 * s, 215 * s), (347 * s, 195 * s)], fill=FOLD)
    # Líneas de texto.
    for x, y, w in ((105, 220, 220), (105, 270, 175)):
        d.rounded_rectangle([x * s, y * s, (x + w) * s, (y + 20) * s],
                            radius=max(1, int(10 * s)), fill=LINE)
    d.line([(305 * s, 310 * s), (340 * s, 345 * s), (405 * s, 275 * s)],
           fill=CHECK, width=max(2, int(26 * s)), joint="curve")
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

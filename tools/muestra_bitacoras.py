#!/usr/bin/env python3
"""Arma un PDF de prueba con unas pocas paginas al azar de ``input/``.

Sirve para probar el recorrido completo (procesar, exportar, indexar en
AirVault) sin arriesgar un batch de cuatrocientas paginas ni esperar el OCR
de un escaneo de setecientos megas.

    portable\\python312\\tools\\python.exe tools\\muestra_bitacoras.py

Sin argumentos toma veinte paginas al azar de los PDF que haya en
``input/`` y deja ``input\\MUESTRA.pdf``. Ese archivo se procesa como
cualquier otro: es una entrada de verdad, no una ejecución reconstruida, asi
que la prueba pasa por el mismo OCR, la misma exportacion y el mismo
indexado que un batch real.

Las paginas salen al azar a proposito: entre ellas caen bitacoras buenas,
alguna en blanco y alguna que el OCR no va a poder leer, que es justo lo
que hay que ver antes de soltar el indexado sobre un batch de verdad.

Esto se corre en la maquina que tiene los escaneos y nunca se commitea lo
que produce: ``input/`` esta fuera del repositorio.
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from app.utils.portable import ensure_portable_env  # noqa: E402

ensure_portable_env()

import pymupdf as fitz  # noqa: E402

ENTRADA = _ROOT / "input"
SALIDA = ENTRADA / "MUESTRA.pdf"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="PDF de prueba con paginas al azar de input/."
    )
    parser.add_argument("--cuantas", type=int, default=20,
                        help="Cuantas paginas (default: 20)")
    parser.add_argument("--semilla", type=int, default=None,
                        help="Semilla, para repetir exactamente la misma "
                             "muestra")
    parser.add_argument("--pdf", default=None,
                        help="Sacar las paginas de un solo PDF, en vez de "
                             "todos los de input/")
    parser.add_argument("--salida", default=str(SALIDA),
                        help="PDF de prueba a escribir")
    return parser.parse_args()


def origenes(uno: str | None, salida: Path) -> list[Path]:
    """Los PDF de los que se puede sacar paginas.

    Se mira solo el primer nivel de ``input/``: lo de ``processed/`` ya se
    proceso una vez y la propia muestra se excluye, o cada ejecución se
    alimentaria de la anterior.
    """
    if uno:
        return [Path(uno)]
    encontrados = [
        pdf for pdf in sorted(ENTRADA.glob("*.pdf"))
        if pdf.resolve() != salida.resolve()
    ]
    if not encontrados:
        raise SystemExit(f"No hay ningun PDF en {ENTRADA}")
    return encontrados


def elegir(fuentes: list[Path], cuantas: int, semilla: int | None):
    """Toma paginas al azar del conjunto, y las devuelve en su orden."""
    canasta: list[tuple[Path, int]] = []
    for pdf in fuentes:
        with fitz.open(pdf) as documento:
            canasta.extend((pdf, numero) for numero in range(len(documento)))
    if len(canasta) < cuantas:
        raise SystemExit(
            f"Entre todos los PDF hay {len(canasta)} paginas; se pidieron "
            f"{cuantas}."
        )
    semilla = semilla if semilla is not None else random.randrange(1, 10 ** 6)
    random.seed(semilla)
    muestra = sorted(random.sample(canasta, cuantas),
                     key=lambda par: (par[0].name, par[1]))
    return muestra, semilla


def copiar(muestra, destino: Path) -> None:
    """Escribe el PDF de prueba con solo las paginas elegidas."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    abiertos: dict[Path, fitz.Document] = {}
    copia = fitz.open()
    try:
        for pdf, numero in muestra:
            if pdf not in abiertos:
                abiertos[pdf] = fitz.open(pdf)
            copia.insert_pdf(abiertos[pdf], from_page=numero, to_page=numero)
        copia.save(str(destino), garbage=4, deflate=True)
    finally:
        copia.close()
        for documento in abiertos.values():
            documento.close()


def main() -> int:
    args = parse_args()
    salida = Path(args.salida)
    muestra, semilla = elegir(
        origenes(args.pdf, salida), args.cuantas, args.semilla
    )
    copiar(muestra, salida)

    print(f"Semilla: {semilla}  (repetible con --semilla {semilla})")
    print(f"Muestra en {salida}  "
          f"({len(muestra)} paginas, "
          f"{salida.stat().st_size / 1_048_576:.1f} MB)")
    print("\nPaginas tomadas:")
    for orden, (pdf, numero) in enumerate(muestra, start=1):
        print(f"  {orden:2d} <- {pdf.name}  pagina {numero + 1}")
    print(f"\nProcesar {salida.name} desde la ventana como cualquier otro "
          "PDF, exportar en un solo PDF e indexar esa ejecución.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

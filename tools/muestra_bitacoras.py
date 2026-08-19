#!/usr/bin/env python3
"""Arma una corrida de prueba con unas pocas bitacoras de una corrida real.

Sirve para probar el indexado en AirVault de punta a punta sin arriesgar un
lote de cuatrocientas paginas: se sacan veinte bitacoras al azar de un PDF
de entrada, se arma con ellas una carpeta de corrida con la misma forma que
deja una corrida de verdad —CSV, indice de paginas y PDF de entrega— y se
indexa esa.

    portable\\python312\\tools\\python.exe tools\\muestra_bitacoras.py ^
        --csv "output\\BITS 18 AUG 2026 05 42\\datos\\BITS 18 AUG 2026 05 42.CSV" ^
        --pdf "input\\Image_001.pdf" ^
        --cuantas 20

La entrega se escribe con la exportacion de verdad, asi que la muestra sale
con sus paginas separadoras y su indice igual que una corrida completa: es
justo lo que hay que probar antes de soltar el indexado sobre un lote real.
Con ``--paginas-por-parte`` la muestra se reparte en varios archivos, uno
por lote, para probar tambien ese camino.

Los PDF de entrada pesan cientos de megas, asi que esto se corre en la
maquina que los tiene y nunca se commitea lo que produce: la carpeta
``output/`` esta fuera del repositorio.
"""

from __future__ import annotations

import argparse
import csv
import random
import shutil
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from app.utils.portable import ensure_portable_env  # noqa: E402

ensure_portable_env()

import pymupdf as fitz  # noqa: E402

from app.models.schemas import FieldResult, PageResult, ValidationReport  # noqa: E402
from app.reports.organize import (  # noqa: E402
    NOMBRE_INDICE_PAGINAS,
    escribir_entrega,
    escribir_indice_paginas,
)

COLUMNAS = (
    "file", "page", "log_number", "dup", "disc", "matricula",
    "flight_number", "pilot_signature", "captain_signature",
    "captain_license", "technician_signature", "date", "time_ms",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Corrida de prueba con unas pocas bitacoras al azar."
    )
    parser.add_argument("--csv", required=True,
                        help="CSV de la corrida (el minimo, no el _completo)")
    parser.add_argument("--pdf", required=True,
                        help="PDF de entrada del que se sacan las paginas")
    parser.add_argument("--cuantas", type=int, default=20,
                        help="Cuantas bitacoras (default: 20)")
    parser.add_argument("--semilla", type=int, default=None,
                        help="Semilla, para repetir exactamente la misma "
                             "muestra")
    parser.add_argument("--paginas-por-parte", type=int, default=0,
                        metavar="N",
                        help="Reparte la entrega en partes de a lo sumo N "
                             "paginas, una por lote")
    parser.add_argument("--sin-separadores", action="store_true",
                        help="No agrupar por matricula; entrega sin paginas "
                             "divisorias")
    parser.add_argument("--salida", default="output/PRUEBA INDEXADO",
                        help="Carpeta de la corrida de prueba")
    return parser.parse_args()


def elegir(filas, nombre_pdf: str, cuantas: int, semilla: int | None):
    """Toma al azar bitacoras utilizables del PDF indicado."""
    candidatas = [
        fila for fila in filas
        if fila.get("file") == nombre_pdf
        and fila.get("log_number")
        and fila.get("matricula")
        and fila.get("date")
    ]
    if len(candidatas) < cuantas:
        raise SystemExit(
            f"Solo hay {len(candidatas)} bitacoras utilizables de "
            f"{nombre_pdf}; se pidieron {cuantas}."
        )
    semilla = semilla if semilla is not None else random.randrange(1, 10 ** 6)
    random.seed(semilla)
    muestra = sorted(
        random.sample(candidatas, cuantas),
        key=lambda fila: int(fila["page"]),
    )
    return muestra, semilla


def copiar_paginas(origen: Path, muestra, destino: Path) -> None:
    """Escribe un PDF con solo las paginas elegidas, en el mismo orden."""
    fuente = fitz.open(origen)
    copia = fitz.open()
    try:
        for fila in muestra:
            indice = int(fila["page"]) - 1
            copia.insert_pdf(fuente, from_page=indice, to_page=indice)
        copia.save(str(destino), garbage=4, deflate=True)
    finally:
        copia.close()
        fuente.close()


def reportes_de_la_muestra(muestra, pdf: Path) -> list[ValidationReport]:
    """Reconstruye lo que la corrida habia leido, para poder reexportarlo.

    Se arman los mismos objetos que produce el procesamiento porque la
    entrega se escribe con la exportacion de verdad: asi la muestra sale con
    las mismas divisorias, el mismo orden y el mismo indice que una corrida
    completa, y la prueba vale para algo.
    """
    paginas = []
    for numero, fila in enumerate(muestra, start=1):
        pagina = PageResult(page_number=numero, date=fila.get("date") or None)
        for campo in ("log_number", "matricula", "flight_number"):
            valor = (fila.get(campo) or "").strip()
            if valor:
                pagina.add_field(FieldResult(
                    page_number=numero, field_id=campo, field_type="ocr",
                    value=valor, confidence=1.0, status="OK",
                ))
        paginas.append(pagina)
    return [ValidationReport(
        pdf_path=str(pdf), template_name="muestra", pages=paginas
    )]


def escribir_csv(muestra, destino: Path, nombre_pdf: str) -> None:
    """CSV con el mismo formato que produce una corrida."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    with destino.open("w", encoding="utf-8-sig", newline="") as handle:
        escritor = csv.DictWriter(handle, fieldnames=list(COLUMNAS))
        escritor.writeheader()
        for numero, fila in enumerate(muestra, start=1):
            copia = {col: fila.get(col, "") for col in COLUMNAS}
            copia["file"] = nombre_pdf
            copia["page"] = numero
            escritor.writerow(copia)


def main() -> int:
    args = parse_args()
    salida = Path(args.salida)
    if salida.exists():
        shutil.rmtree(salida)
    (salida / "datos").mkdir(parents=True)

    with Path(args.csv).open(encoding="utf-8-sig", newline="") as handle:
        filas = list(csv.DictReader(handle))
    muestra, semilla = elegir(
        filas, Path(args.pdf).name, args.cuantas, args.semilla
    )

    # Las paginas elegidas, en un PDF aparte que hace de original: la
    # exportacion copia de ahi igual que copiaria del escaneo entero.
    fuente = salida / "datos" / "paginas.pdf"
    copiar_paginas(Path(args.pdf), muestra, fuente)

    escribir_csv(muestra, salida / "datos" / f"{salida.name}.CSV",
                 fuente.name)

    reportes = reportes_de_la_muestra(muestra, fuente)
    partes = escribir_entrega(
        reportes, salida,
        separar_por=() if args.sin_separadores else ("avion",),
        paginas_por_parte=args.paginas_por_parte,
    )
    escribir_indice_paginas(
        partes, salida / "datos" / f"{salida.name}{NOMBRE_INDICE_PAGINAS}"
    )

    print(f"Semilla: {semilla}  (repetible con --semilla {semilla})")
    print(f"Corrida de prueba en {salida}")
    print(f"  CSV:     datos/{salida.name}.CSV  ({len(muestra)} bitacoras)")
    print(f"  Indice:  datos/{salida.name}{NOMBRE_INDICE_PAGINAS}")
    for ruta, tramo in partes:
        divisorias = sum(1 for e in tramo if e.es_separador)
        print(f"  Entrega: {ruta.name}  ({len(tramo)} paginas, "
              f"{divisorias} separadoras, "
              f"{ruta.stat().st_size / 1_048_576:.1f} MB)")
    print("\nPaginas tomadas del original:")
    for numero, fila in enumerate(muestra, start=1):
        print(f"  {numero:2d} <- pagina {fila['page']:>4}  "
              f"{fila['matricula']}  {fila['log_number']}  {fila['date']}")
    print("\nPara indexarla, apuntar la seccion de AirVault a "
          f"{salida / 'datos' / (salida.name + '.CSV')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

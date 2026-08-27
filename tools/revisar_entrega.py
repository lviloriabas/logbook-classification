#!/usr/bin/env python3
r"""Dice si el CSV y el PDF de la entrega hablan de las mismas paginas.

El indexado empareja cada pagina del PDF con su fila del CSV por
``(archivo, pagina)``. Si los dos no usan el mismo nombre de archivo, el
manifiesto sale con todos los registros vacios y AirVault recibe un batch
sin matricula, sin log y sin fecha: cuatrocientas paginas amarillas que el
indexado se niega a escribir.

Esto lo comprueba sin tocar la red ni AirVault:

    portable\python312\tools\python.exe tools\revisar_entrega.py "output\BITS 27 AUG 2026 11 08"

Sin argumentos toma la ejecucion mas reciente de ``output/``. Imprime los
nombres que usa cada archivo, cuantas paginas emparejan y por que camino, y
—si ya existe— cuantos registros vacios quedaron en el manifiesto.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from app.airvault.mapping import (  # noqa: E402
    _sin_sufijo_de_copia,
    leer_csv_corrida,
    registros_desde_entrega,
)
from app.reports.organize import NOMBRE_INDICE_PAGINAS  # noqa: E402


def ultima_ejecucion() -> Path | None:
    """La carpeta de ``output/`` con un CSV de ejecucion mas reciente."""
    salidas = RAIZ / "output"
    candidatas = [
        carpeta for carpeta in salidas.iterdir()
        if carpeta.is_dir() and (carpeta / "datos").is_dir()
    ] if salidas.is_dir() else []
    return max(candidatas, key=lambda c: c.stat().st_mtime, default=None)


def paginas_del_indice(indice: dict) -> list[dict]:
    """Todas las paginas declaradas, de todas las partes de la entrega."""
    paginas: list[dict] = []
    for parte in indice.get("partes") or []:
        paginas.extend(parte.get("paginas") or [])
    return paginas


def main() -> int:
    carpeta = Path(sys.argv[1]) if len(sys.argv) > 1 else ultima_ejecucion()
    if carpeta is None or not carpeta.is_dir():
        print("No encontre la carpeta de la ejecucion.")
        return 1
    print(f"Ejecucion: {carpeta}")

    datos = carpeta / "datos"
    csv_path = datos / f"{carpeta.name}.CSV"
    indice_path = datos / f"{carpeta.name}{NOMBRE_INDICE_PAGINAS}"
    for ruta in (csv_path, indice_path):
        if not ruta.is_file():
            print(f"  FALTA {ruta.name}")
            return 1

    filas = leer_csv_corrida(csv_path)
    nombres_csv = Counter(str(f.get("file", "")).strip() for f in filas)
    print(f"\nCSV: {len(filas)} filas")
    for nombre, cuantas in nombres_csv.most_common():
        print(f"  file = {nombre!r}  ({cuantas} paginas)")
    columnas = list(filas[0]) if filas else []
    print(f"  columnas = {columnas}")
    if not {"file", "page", "matricula", "log_number", "date"} <= set(columnas):
        print("  OJO: al CSV le faltan columnas que el indexado necesita")
    for fila in filas[:2]:
        print(f"  fila: {dict(fila)}")

    indice = json.loads(indice_path.read_text(encoding="utf-8"))
    paginas = paginas_del_indice(indice)
    bitacoras = [p for p in paginas if not p.get("separador")]
    nombres_indice = Counter(
        str(p.get("archivo", "")).strip() for p in bitacoras
    )
    print(f"\nIndice: {len(paginas)} paginas, {len(bitacoras)} bitacoras")
    for nombre, cuantas in nombres_indice.most_common():
        print(f"  archivo = {nombre!r}  ({cuantas} paginas)")
    for entrada in bitacoras[:2]:
        print(f"  entrada: {entrada}")

    exactas = {(str(f.get("file", "")).strip(), str(f.get("page", "")).strip())
               for f in filas}
    por_camino = Counter()
    for pagina in bitacoras:
        archivo = str(pagina.get("archivo", "")).strip()
        numero = str(pagina.get("pagina", "")).strip()
        if (archivo, numero) in exactas:
            por_camino["por nombre y pagina"] += 1
        elif (_sin_sufijo_de_copia(archivo), numero) in exactas:
            por_camino["quitando el sufijo -N"] += 1
        elif len(nombres_csv) == 1 and any(
            numero == p for _a, p in exactas
        ):
            por_camino["solo por la pagina (un unico PDF)"] += 1
        else:
            por_camino["SIN FILA"] += 1
    print("\nEmparejamiento:")
    for camino, cuantas in por_camino.most_common():
        print(f"  {cuantas:5d}  {camino}")

    registros = registros_desde_entrega(filas, paginas)
    vacios = [
        r for r in registros
        if not r.es_separador and not r.matricula and not r.log_number
    ]
    print(
        f"\nCon el codigo de hoy: {len(registros)} registros, "
        f"{len(vacios)} vacios"
    )
    if vacios:
        print("  Con registros vacios el indexado no escribe nada.")
        return 2
    print("  El manifiesto se armaria completo.")

    for manifiesto in sorted(
        (RAIZ / "output" / "airvault" / carpeta.name).glob("**/manifiesto.json")
    ):
        guardado = json.loads(manifiesto.read_text(encoding="utf-8"))
        suyos = [
            r for r in guardado.get("registros", [])
            if not r.get("separador")
        ]
        rotos = [r for r in suyos if not r.get("matricula")
                 and not r.get("log_number")]
        estado = "hay que rehacerlo" if rotos else "esta bien"
        print(
            f"\nManifiesto guardado {manifiesto.parent.name}: "
            f"{len(rotos)} de {len(suyos)} vacios; {estado}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

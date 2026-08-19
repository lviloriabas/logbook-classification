"""Reporte de revision: que se va a escribir y que quedo bloqueado.

Es el mismo artefacto en los tres modos. En dry run es el resultado final;
en modo revision es lo que hay que aprobar; en automatico queda como
constancia de lo que se hizo.
"""

from __future__ import annotations

import csv
import html
from pathlib import Path
from typing import Sequence

from app.airvault.config import (
    CAMPO_AUDIT_STATUS,
    CAMPO_DOC_TYPE,
    CAMPO_END_DATE,
    CAMPO_FLEET,
    CAMPO_LOG_NUMBER,
    CAMPO_MATRICULA,
)
from app.airvault.indexer import Plan

COLUMNAS = (
    "pagina_lote", "seq", "archivo_origen", "pagina_origen",
    "doc_type", "matricula", "fleet", "fleet_inferido", "log_number",
    "audit_status", "end_date", "ya_indexada", "accion", "avisos",
)


def _fila(entrada) -> dict:
    registro = entrada.registro
    valores = entrada.valores
    return {
        "pagina_lote": entrada.pagina_batch,
        "seq": entrada.seq,
        "archivo_origen": registro.archivo_origen,
        "pagina_origen": registro.pagina_origen,
        "doc_type": valores.get(CAMPO_DOC_TYPE, ""),
        "matricula": valores.get(CAMPO_MATRICULA, ""),
        "fleet": valores.get(CAMPO_FLEET, ""),
        "fleet_inferido": "si" if registro.fleet_inferido else "",
        "log_number": valores.get(CAMPO_LOG_NUMBER, ""),
        "audit_status": valores.get(CAMPO_AUDIT_STATUS, ""),
        "end_date": valores.get(CAMPO_END_DATE, ""),
        "ya_indexada": "si" if entrada.ya_indexada else "",
        "accion": "escribir" if entrada.escribible else "bloqueada",
        "avisos": " | ".join(str(a) for a in entrada.avisos),
    }


def escribir_csv(plan: Plan, destino: Path | str) -> Path:
    """Vuelca el plan a CSV, con BOM para que Excel lo abra bien."""
    ruta = Path(destino)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with ruta.open("w", encoding="utf-8-sig", newline="") as handle:
        escritor = csv.DictWriter(handle, fieldnames=list(COLUMNAS))
        escritor.writeheader()
        for entrada in plan.paginas:
            escritor.writerow(_fila(entrada))
    return ruta


def escribir_html(plan: Plan, destino: Path | str, titulo: str = "") -> Path:
    """Vuelca el plan a una pagina HTML de una sola vista.

    Sin dependencias ni recursos externos: el archivo se abre con doble
    clic en cualquier maquina, tambien sin internet.
    """
    ruta = Path(destino)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    resumen = plan.resumen()
    filas = []
    for entrada in plan.paginas:
        datos = _fila(entrada)
        clase = "bloqueada" if not entrada.escribible else ""
        celdas = "".join(
            f"<td>{html.escape(str(datos[col]))}</td>" for col in COLUMNAS
        )
        filas.append(f'<tr class="{clase}">{celdas}</tr>')
    encabezados = "".join(f"<th>{html.escape(c)}</th>" for c in COLUMNAS)
    contenido = f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<title>{html.escape(titulo or 'Revision de indexado')}</title>
<style>
 body {{ font-family: Segoe UI, Arial, sans-serif; margin: 24px;
         color: #202020; }}
 h1 {{ font-size: 20px; margin: 0 0 4px; }}
 .resumen {{ margin: 12px 0 20px; font-size: 14px; }}
 .resumen span {{ display: inline-block; margin-right: 18px; }}
 table {{ border-collapse: collapse; font-size: 13px; width: 100%; }}
 th, td {{ border: 1px solid #d8d8d8; padding: 4px 8px; text-align: left; }}
 th {{ background: #f2f2f2; position: sticky; top: 0; }}
 tr.bloqueada {{ background: #fff4f4; }}
 tr:nth-child(even):not(.bloqueada) {{ background: #fafafa; }}
</style></head><body>
<h1>{html.escape(titulo or 'Revision de indexado')}</h1>
<div class="resumen">
 <span>Lote: <b>{html.escape(plan.batch_id)}</b></span>
 <span>Paginas: <b>{resumen['total']}</b></span>
 <span>Se escribirian: <b>{resumen['escribibles']}</b></span>
 <span>Bloqueadas: <b>{resumen['bloqueadas']}</b></span>
</div>
<table><thead><tr>{encabezados}</tr></thead>
<tbody>{''.join(filas)}</tbody></table>
</body></html>
"""
    ruta.write_text(contenido, encoding="utf-8")
    return ruta


def resumen_texto(plan: Plan) -> str:
    """Resumen corto para la consola."""
    datos = plan.resumen()
    lineas = [
        f"Lote {plan.batch_id}: {datos['total']} paginas",
        f"  se escribirian: {datos['escribibles']}",
        f"  bloqueadas:     {datos['bloqueadas']}",
    ]
    motivos: dict[str, int] = {}
    for entrada in plan.bloqueadas:
        for aviso in entrada.avisos:
            motivos[aviso.codigo] = motivos.get(aviso.codigo, 0) + 1
    for codigo, cuantas in sorted(motivos.items(), key=lambda x: -x[1]):
        lineas.append(f"    {codigo}: {cuantas}")
    return "\n".join(lineas)

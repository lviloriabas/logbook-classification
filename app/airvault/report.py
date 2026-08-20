"""Reporte de revision: que se va a escribir y que quedo bloqueado.

Es el mismo artefacto en los tres modos. En dry run es el resultado final;
en modo revision es lo que hay que aprobar; en automatico queda como
constancia de lo que se hizo.
"""

from __future__ import annotations

import csv
import html
from pathlib import Path
from typing import Sequence, Tuple

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
    "audit_status", "end_date", "fecha_inferida", "ya_indexada",
    "accion", "avisos",
)

# Con la corrida repartida en varios lotes, saber en cual cae cada pagina
# es lo primero que hace falta para ir a mirarla.
COLUMNAS_CON_LOTE = ("lote",) + COLUMNAS


def _lotes(partes: Sequence[Tuple[str, "Plan"]]) -> str:
    return ", ".join(
        nombre or plan.batch_id or "sin asignar" for nombre, plan in partes
    ) or "sin asignar"


def _resumen_sumado(partes: Sequence[Tuple[str, "Plan"]]) -> dict:
    """Suma los resumenes de todas las partes."""
    total: dict = {"total": 0, "escribibles": 0, "bloqueadas": 0,
                   "separadores": 0, "avisos_globales": 0,
                   "fechas_inferidas": 0}
    for _nombre, plan in partes:
        for clave, valor in plan.resumen().items():
            total[clave] = total.get(clave, 0) + valor
    return total


def _fila(entrada) -> dict:
    registro = entrada.registro
    valores = entrada.valores
    return {
        "pagina_lote": entrada.pagina_batch,
        "seq": entrada.seq,
        "archivo_origen": registro.archivo_origen,
        "pagina_origen": registro.pagina_origen,
        "doc_type": valores.get(CAMPO_DOC_TYPE, ""),
        "matricula": registro.separador or valores.get(CAMPO_MATRICULA, ""),
        "fleet": valores.get(CAMPO_FLEET, ""),
        "fleet_inferido": "si" if registro.fleet_inferido else "",
        "log_number": valores.get(CAMPO_LOG_NUMBER, ""),
        "audit_status": valores.get(CAMPO_AUDIT_STATUS, ""),
        "end_date": valores.get(CAMPO_END_DATE, ""),
        # Como se dedujo la fecha cuando la bitacora no la trajo leida.
        # Vacio es lo normal: la fecha salio de la pagina.
        "fecha_inferida": registro.fecha_inferida,
        "ya_indexada": "si" if entrada.ya_indexada else "",
        "accion": (
            "separador" if registro.es_separador
            else "escribir" if entrada.escribible
            else "bloqueada"
        ),
        "avisos": " | ".join(str(a) for a in entrada.avisos),
    }


def escribir_csv(plan: Plan, destino: Path | str) -> Path:
    """Vuelca el plan a CSV, con BOM para que Excel lo abra bien."""
    return escribir_csv_de_partes([("", plan)], destino)


def escribir_csv_de_partes(
    partes: Sequence[Tuple[str, Plan]], destino: Path | str
) -> Path:
    """Vuelca a un solo CSV lo que se escribiria en todas las partes.

    Una corrida repartida son varios lotes, pero se aprueba de una vez: el
    reporte tiene que dejar ver la corrida entera y no obligar a abrir cinco
    archivos para saber que se va a escribir.
    """
    ruta = Path(destino)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    columnas = list(COLUMNAS_CON_LOTE if len(partes) > 1 else COLUMNAS)
    with ruta.open("w", encoding="utf-8-sig", newline="") as handle:
        escritor = csv.DictWriter(handle, fieldnames=columnas)
        escritor.writeheader()
        for nombre, plan in partes:
            for entrada in plan.paginas:
                fila = _fila(entrada)
                if len(partes) > 1:
                    fila = {"lote": nombre or plan.batch_id, **fila}
                escritor.writerow(fila)
    return ruta


def escribir_html(plan: Plan, destino: Path | str, titulo: str = "") -> Path:
    """Vuelca el plan a una pagina HTML de una sola vista."""
    return escribir_html_de_partes([("", plan)], destino, titulo)


def escribir_html_de_partes(
    partes: Sequence[Tuple[str, Plan]], destino: Path | str,
    titulo: str = "",
) -> Path:
    """Vuelca a una sola pagina lo que se escribiria en todas las partes.

    Sin dependencias ni recursos externos: el archivo se abre con doble
    clic en cualquier maquina, tambien sin internet.
    """
    ruta = Path(destino)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    resumen = _resumen_sumado(partes)
    varias = len(partes) > 1
    columnas = list(COLUMNAS_CON_LOTE if varias else COLUMNAS)
    filas = []
    for nombre, plan in partes:
        if varias:
            propio = plan.resumen()
            filas.append(
                f'<tr class="cabecera"><td colspan="{len(columnas)}">'
                f'{html.escape(nombre or plan.batch_id)} — '
                f'{propio["total"]} paginas, {propio["escribibles"]} se '
                f'escribirian, {propio["bloqueadas"]} bloqueadas'
                f'</td></tr>'
            )
        for entrada in plan.paginas:
            datos = _fila(entrada)
            if varias:
                datos = {"lote": nombre or plan.batch_id, **datos}
            if entrada.registro.es_separador:
                clase = "separador"
            elif not entrada.escribible:
                clase = "bloqueada"
            else:
                clase = ""
            celdas = "".join(
                f"<td>{html.escape(str(datos[col]))}</td>" for col in columnas
            )
            filas.append(f'<tr class="{clase}">{celdas}</tr>')
    encabezados = "".join(f"<th>{html.escape(c)}</th>" for c in columnas)
    plan = partes[0][1] if partes else Plan(batch_id="")
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
 tr.separador {{ background: #f4f4f4; color: #6a6a6a; }}
 tr.cabecera td {{ background: #eaeaea; font-weight: 600; padding: 8px; }}
 tr:nth-child(even):not(.bloqueada) {{ background: #fafafa; }}
</style></head><body>
<h1>{html.escape(titulo or 'Revision de indexado')}</h1>
<div class="resumen">
 <span>{'Lotes' if len(partes) > 1 else 'Lote'}: <b>{html.escape(_lotes(partes))}</b></span>
 <span>Paginas: <b>{resumen['total']}</b></span>
 <span>Se escribirian: <b>{resumen['escribibles']}</b></span>
 <span>Bloqueadas: <b>{resumen['bloqueadas']}</b></span>
 <span>Separadores: <b>{resumen['separadores']}</b></span>
 <span>Fecha deducida: <b>{resumen['fechas_inferidas']}</b></span>
</div>
<table><thead><tr>{encabezados}</tr></thead>
<tbody>{''.join(filas)}</tbody></table>
</body></html>
"""
    ruta.write_text(contenido, encoding="utf-8")
    return ruta


def resumen_texto(plan: Plan) -> str:
    """Resumen corto para la consola."""
    return resumen_texto_de_partes([("", plan)])


def resumen_texto_de_partes(partes: Sequence[Tuple[str, Plan]]) -> str:
    """Resumen corto de la corrida entera, parte por parte."""
    datos = _resumen_sumado(partes)
    cabeza = (
        f"{len(partes)} lotes: {datos['total']} paginas"
        if len(partes) > 1
        else f"Lote {_lotes(partes)}: {datos['total']} paginas"
    )
    lineas = [
        cabeza,
        f"  se escribirian: {datos['escribibles']}",
        f"  bloqueadas:     {datos['bloqueadas']}",
        f"  separadores:    {datos['separadores']}",
    ]
    if datos["fechas_inferidas"]:
        lineas.append(f"  fecha deducida: {datos['fechas_inferidas']}")
    motivos: dict[str, int] = {}
    for _nombre, plan in partes:
        for entrada in plan.bloqueadas:
            for aviso in entrada.avisos:
                motivos[aviso.codigo] = motivos.get(aviso.codigo, 0) + 1
    for codigo, cuantas in sorted(motivos.items(), key=lambda x: -x[1]):
        lineas.append(f"    {codigo}: {cuantas}")
    return "\n".join(lineas)

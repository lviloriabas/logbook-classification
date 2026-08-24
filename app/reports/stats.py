"""Estadísticas de la ejecución (``stats.json``).

Resume la distribución de las páginas de bitácora de la ejecución: totales,
conteo por matrícula y por mes, discrepancias de firma y las páginas que
no se pudieron determinar por fecha (``sf``) o por matrícula
(``sin_matricula``).

Cuando se generan PDFs por matrícula/mes, el bloque ``separacion``
detalla cada PDF con su número de páginas y la verificación de que
**ninguna bitácora queda por fuera**:

    total_paginas = en_blanco + excluidas_por_discrepancia
                    + distribuidas + fuera     (fuera debe ser 0)
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

from loguru import logger

from app.models.schemas import ValidationReport
from app.reports.organize import (
    NOMBRE_PDF_REVISAR,
    agrupar_paginas,
    clave_avion,
    clave_mes,
    iterar_paginas,
    paginas_para_revisar,
    por_revisar,
    ruta_pdf,
)
from app.validation.discrepancias import Categoria, Discrepancia


def _conteo_sin_determinar(reports: Sequence[ValidationReport]
                           ) -> Tuple[Dict[str, dict], Dict[str, int], int, int]:
    """Conteo por matrícula/mes de las páginas válidas (no en blanco).

    Returns:
        (por_matricula, por_mes, sin_matricula, sin_fecha): diccionarios
        de conteo y número de páginas sin matrícula/fecha determinable.
    """
    por_matricula: Dict[str, dict] = {}
    por_mes: Dict[str, int] = {}
    sin_matricula = 0
    sin_fecha = 0
    for ref in iterar_paginas(reports):
        if ref.page.blank:
            continue
        matricula = clave_avion(ref.page)
        mes = clave_mes(ref.page)
        if mes == "sin_fecha":
            mes = "sf"
        if matricula == "sin_matricula":
            sin_matricula += 1
        if mes == "sf":
            sin_fecha += 1
        entrada = por_matricula.setdefault(
            matricula, {"total": 0, "por_mes": {}}
        )
        entrada["total"] += 1
        entrada["por_mes"][mes] = entrada["por_mes"].get(mes, 0) + 1
        por_mes[mes] = por_mes.get(mes, 0) + 1
    for entrada in por_matricula.values():
        entrada["por_mes"] = dict(sorted(entrada["por_mes"].items()))
    return por_matricula, por_mes, sin_matricula, sin_fecha


def _stats_discrepancias(entradas: Sequence[Discrepancia]) -> dict:
    """Bloque de discrepancias: totales, por matrícula y detalle."""
    faltantes = sum(1 for e in entradas if e.categoria is Categoria.MISSING)
    por_matricula: Dict[str, int] = {}
    for entrada in entradas:
        matricula = entrada.matricula or "sin_matricula"
        por_matricula[matricula] = por_matricula.get(matricula, 0) + 1
    return {
        "total": len(entradas),
        "faltantes": faltantes,
        "incierta": len(entradas) - faltantes,
        "por_matricula": dict(sorted(por_matricula.items())),
        "detalle": [
            {
                "bitacora": Path(e.pdf_path).name,
                "pagina": e.page_number,
                "matricula": e.matricula or "sin_matricula",
                "log_number": e.log_number,
                "tipo": e.tipo.value,
                "categoria": e.categoria.value,
                "razones": e.razones(),
            }
            for e in entradas
        ],
    }


def _stats_vlm(vlm_stats: Sequence[dict]) -> dict:
    """Bloque del verificador VLM: cuántos casos resolvió la ejecución."""
    activos = [v for v in vlm_stats if v.get("enabled")]
    modelos = sorted({v["model"] for v in activos if v.get("model")})
    desactivado = next(
        (v.get("disabled") for v in vlm_stats if v.get("disabled")),
        None,
    )
    return {
        "activo": bool(activos),
        "bitacoras_con_vlm": len(activos),
        "modelos": modelos,
        "date_targets": sum(v.get("date_targets", 0) for v in activos),
        "date_fields_resueltos": sum(
            v.get("date_fields_resolved", 0) for v in activos
        ),
        "crops_consultados": sum(v.get("crops", 0) for v in activos),
        "firmas_resueltas": sum(
            v.get("signatures_resolved", 0) for v in activos
        ),
        "campos_resueltos": sum(
            v.get("fields_resolved", 0) for v in activos
        ),
        "desactivado_por": desactivado,
    }


def _stats_separacion(
    reports: Sequence[ValidationReport],
    separar_por: Sequence[str],
    excluidas: Optional[Set[Tuple[str, int]]],
    total_paginas: int,
    paginas_en_blanco: int,
    pdf_paths: Optional[Sequence[Path]] = None,
) -> dict:
    """Bloque de separación: PDFs generados y verificación de conteo.

    ``pdf_paths`` son las rutas realmente escritas, en el mismo orden que
    ``sorted(grupos)`` que usa ``generar_pdfs``. Se prefieren al nombre
    teórico porque un re-export conserva los PDFs anteriores y numera los
    nuevos; sin ellas (o si no cuadran con los grupos) se cae al nombre
    que le correspondería al grupo.
    """
    excluidas = excluidas or set()
    grupos = agrupar_paginas(reports, separar_por, excluidas)
    revisar = paginas_para_revisar(reports)
    claves = sorted(grupos)
    reales = list(pdf_paths or [])
    # ``generar_pdfs`` escribe los grupos y, detrás, el PDF de revisión.
    nombres = (
        [ruta.name for ruta in reales]
        if len(reales) == len(claves) + bool(revisar)
        else [ruta_pdf(clave, separar_por).as_posix() for clave in claves]
        + ([NOMBRE_PDF_REVISAR] if revisar else [])
    )
    pdfs: List[dict] = []
    distribuidas = 0
    for clave, nombre in zip(claves, nombres):
        paginas = len(grupos[clave])
        distribuidas += paginas
        pdfs.append({"archivo": nombre, "paginas": paginas})
    if revisar:
        # Las bitácoras cuya matrícula requiere revisión no se pierden: salen
        # siempre en su propio PDF, así que cuentan como distribuidas.
        pdfs.append({"archivo": nombres[-1], "paginas": len(revisar)})
        distribuidas += len(revisar)
    excluidas_count = sum(
        1
        for ref in iterar_paginas(reports)
        if not ref.page.blank
        and not por_revisar(ref.page)
        and (Path(ref.pdf_path).name, ref.page.page_number) in excluidas
    )
    # Las páginas en blanco forman parte de REVISAR y ya están contadas en
    # ``distribuidas``. Restarlas otra vez produciría un total negativo.
    fuera = total_paginas - excluidas_count - distribuidas
    return {
        "criterios": list(separar_por),
        "total_pdfs": len(pdfs),
        "pdfs": pdfs,
        "paginas_distribuidas": distribuidas,
        "paginas_excluidas_por_discrepancia": excluidas_count,
        "paginas_fuera": fuera,
        "completa": fuera == 0,
    }


def construir_stats(
    reports: Sequence[ValidationReport],
    corrida: Optional[str] = None,
    separar_por: Optional[Sequence[str]] = None,
    entradas: Optional[Sequence[Discrepancia]] = None,
    excluidas: Optional[Set[Tuple[str, int]]] = None,
    vlm_stats: Optional[Sequence[dict]] = None,
    pdf_paths: Optional[Sequence[Path]] = None,
) -> dict:
    """Construye el diccionario de estadísticas de la ejecución.

    Args:
        reports: Reportes de validación (uno por bitácora procesada).
        ejecución: Nombre de la ejecución (stem del CSV).
        separar_por: Criterios de separación usados (``avion``/``mes``);
            si se generaron PDFs se añade el bloque ``separacion`` con la
            verificación de que ninguna página queda por fuera.
        entradas: Discrepancias de firma clasificadas (si se calcularon).
        excluidas: Páginas excluidas de los PDFs por discrepancia.
        vlm_stats: Stats del verificador VLM por bitácora (si se ejecutó).
        pdf_paths: Rutas de los PDFs realmente escritos, para que el bloque
            ``separacion`` nombre los archivos que existen en disco.

    Returns:
        Diccionario listo para serializar como ``stats.json``.
    """
    reports = list(reports)
    total_paginas = sum(len(r.pages) for r in reports)
    en_blanco = sum(1 for r in reports for p in r.pages if p.blank)
    por_matricula, por_mes, sin_matricula, sin_fecha = \
        _conteo_sin_determinar(reports)

    stats = {
        "corrida": corrida,
        "generado": datetime.now().isoformat(timespec="seconds"),
        "total_bitacoras": len(reports),
        "bitacoras": [
            {"archivo": Path(r.pdf_path).name, "paginas": len(r.pages)}
            for r in reports
        ],
        "total_paginas": total_paginas,
        "paginas_en_blanco": en_blanco,
        "paginas_validas": total_paginas - en_blanco,
        "por_matricula": dict(sorted(por_matricula.items())),
        "por_mes": dict(sorted(por_mes.items())),
        "sin_matricula": sin_matricula,
        "sin_fecha": sin_fecha,
        "discrepancias": _stats_discrepancias(list(entradas or [])),
        "vlm": _stats_vlm(list(vlm_stats or [])),
    }
    if separar_por:
        stats["separacion"] = _stats_separacion(
            reports, separar_por, excluidas, total_paginas, en_blanco,
            pdf_paths,
        )
    return stats


def escribir_stats(
    reports: Sequence[ValidationReport],
    run_dir: Path,
    corrida: Optional[str] = None,
    separar_por: Optional[Sequence[str]] = None,
    entradas: Optional[Sequence[Discrepancia]] = None,
    excluidas: Optional[Set[Tuple[str, int]]] = None,
    vlm_stats: Optional[Sequence[dict]] = None,
    pdf_paths: Optional[Sequence[Path]] = None,
) -> Path:
    """Escribe ``stats.json`` en la carpeta de la ejecución.

    Returns:
        Ruta del archivo generado (``run_dir/stats.json``).
    """
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    stats = construir_stats(
        reports,
        corrida=corrida,
        separar_por=separar_por,
        entradas=entradas,
        excluidas=excluidas,
        vlm_stats=vlm_stats,
        pdf_paths=pdf_paths,
    )
    output_path = run_dir / "stats.json"
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(stats, fh, indent=2, ensure_ascii=False)
    logger.info(f"[Stats] Estadísticas de la ejecución: {output_path}")
    separacion = stats.get("separacion")
    if separacion and not separacion["completa"]:
        logger.warning(
            f"[Stats] {separacion['paginas_fuera']} página(s) quedaron "
            "fuera de los PDFs generados"
        )
    return output_path

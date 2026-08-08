"""Generación de todas las salidas de una corrida.

Este módulo no depende de Qt. La función pública puede ejecutarse en un
hilo de fondo sin leer ni modificar widgets de la interfaz.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional, Sequence

from loguru import logger

from app.models.schemas import ValidationReport
from app.reports.csv_reporter import CsvReporter
from app.reports.debug_pdf import write_debug_pdf
from app.reports.json_reporter import JsonReporter
from app.templates.schema import Template


_MONTHS = [
    "JAN", "FEB", "MAR", "APR", "MAY", "JUN",
    "JUL", "AUG", "SEP", "OCT", "NOV", "DEC",
]


@dataclass(frozen=True)
class OutputOptions:
    """Opciones inmutables necesarias para escribir las salidas."""

    template: Template
    output_root: Path
    dpi: int
    crop_padding: float
    separar_por: tuple[str, ...] = ()
    un_solo_pdf: bool = False
    discrepancias: bool = False
    debug: bool = False
    run_dir: Path | None = None
    skip_pdfs: bool = False


def run_csv_name() -> str:
    """Nombre de corrida en el formato ``BITS <DD MON YYYY> <HH MM>.CSV``."""
    now = datetime.now()
    stamp = now.strftime(f"%d {_MONTHS[now.month - 1]} %Y %H %M").upper()
    return f"BITS {stamp}.CSV"


def _clean_stale_artifacts(run_dir: Path) -> None:
    """Borra los artefactos de una exportación anterior en la misma corrida.

    Conserva ``datos/`` (se sobreescribe) y ``logs/``. Elimina los PDFs de
    la separación previa (incluidas carpetas por matrícula), stats, debug,
    discrepancias y recortes, para que un re-export con opciones nuevas no
    deje archivos que ya no corresponden.
    """
    keep = {"datos", "logs"}
    for child in run_dir.iterdir():
        if child.name in keep:
            continue
        try:
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
        except OSError as exc:  # noqa: BLE001 - archivo en uso
            logger.warning(f"No se pudo limpiar {child}: {exc}")


def write_outputs(
    reports: Sequence[ValidationReport],
    options: OutputOptions,
    vlm_stats: Sequence[dict] | None = None,
    on_stage: Optional[Callable[[str, int], None]] = None,
) -> Path:
    """Escribe los reportes y PDFs de una corrida completa.

    Todas las operaciones son de disco/renderizado y no deben ejecutarse en
    el hilo de la interfaz. ``on_stage`` recibe (mensaje, porcentaje 0-100)
    al avanzar de cada fase.

    Si ``options.run_dir`` viene, la corrida se escribe SOBRE esa carpeta
    (mismo nombre de CSV y mismo carpeta de corrida): es el modo re-export,
    usado por la GUI para regenerar las salidas sin crear una corrida nueva.
    Si no, se crea una carpeta de corrida nueva con timestamp.

    Con ``options.skip_pdfs`` (corrida cancelada a mitad de camino) se
    guardan solo los datos (CSV, JSON, stats) y NO se generan PDFs, para
    que la corrida quede guardada exactamente hasta donde se canceló.
    """

    def stage(message: str, percent: int) -> None:
        if on_stage is not None:
            on_stage(message, percent)

    reports = list(reports)
    template = options.template
    output_root = Path(options.output_root)
    skip_pdfs = options.skip_pdfs

    if options.run_dir is not None:
        run_dir = Path(options.run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        corrida = run_dir.name
        csv_name = f"{corrida}.CSV"
        _clean_stale_artifacts(run_dir)
        logger.info(f"Re-export sobre la corrida existente: {run_dir}")
    else:
        csv_name = run_csv_name()
        corrida = Path(csv_name).stem
        base_corrida = corrida
        n = 2
        while (output_root / corrida).exists():
            corrida = f"{base_corrida}-{n}"
            n += 1
        csv_name = f"{corrida}.CSV"
        run_dir = output_root / corrida
        run_dir.mkdir(parents=True, exist_ok=True)

    datos_dir = run_dir / "datos"
    datos_dir.mkdir(parents=True, exist_ok=True)

    from app.validation.discrepancias import clasificar_lote

    entradas = clasificar_lote(reports, template)
    excluidas: set[tuple[str, int]] = set()
    if options.discrepancias:
        excluidas = {
            (Path(entrada.pdf_path).name, entrada.page_number)
            for entrada in entradas
        }

    stage("Escribiendo CSV…", 10)
    csv_path = datos_dir / csv_name
    CsvReporter().write(reports, csv_path, template)
    stage("Escribiendo JSON…", 20)
    json_path = datos_dir / f"{corrida}.json"
    JsonReporter().write_consolidated(reports, json_path, corrida=corrida)

    if options.debug and not skip_pdfs:
        stage("Generando debug.pdf…", 35)
        write_debug_pdf(
            reports,
            template,
            run_dir / "debug.pdf",
            dpi=options.dpi,
            crop_padding=options.crop_padding,
        )

    separar = list(options.separar_por) or None
    if options.discrepancias and not skip_pdfs:
        stage("Generando discrepancias.pdf…", 45)
        from app.reports.organize import escribir_pdf_discrepancias

        escribir_pdf_discrepancias(
            entradas, template, run_dir, dpi=options.dpi
        )

    if skip_pdfs:
        pdf_paths: list[Path] = []
        stage("Guardando datos (sin PDFs)…", 60)
    else:
        from app.reports.organize import escribir_pdf_unico, generar_pdfs

        stage("Organizando PDFs…", 60)
        if options.un_solo_pdf:
            pdf_paths = [
                escribir_pdf_unico(
                    reports,
                    run_dir,
                    separar or [],
                    excluidas,
                    dpi=options.dpi,
                )
            ]
            logger.info(f"PDF único: {pdf_paths[0].relative_to(run_dir)}")
        elif separar:
            pdf_paths = generar_pdfs(
                reports,
                run_dir,
                separar,
                excluidas,
                dpi=options.dpi,
            )
            logger.info(f"PDFs separados: {len(pdf_paths)}")
            for pdf_path in pdf_paths:
                logger.info(f"  - {pdf_path.relative_to(run_dir)}")
        else:
            pdf_paths = [
                escribir_pdf_unico(
                    reports,
                    run_dir,
                    [],
                    excluidas,
                    dpi=options.dpi,
                )
            ]
            logger.info(f"PDF único: {pdf_paths[0].relative_to(run_dir)}")

    from app.reports.stats import escribir_stats

    stage("Calculando estadísticas…", 85)
    stats_path = escribir_stats(
        reports,
        run_dir,
        corrida=corrida,
        separar_por=None if skip_pdfs else separar,
        entradas=entradas,
        excluidas=excluidas or None,
        vlm_stats=vlm_stats,
    )
    logger.info(f"Stats de la corrida: {stats_path}")
    stage("Finalizando…", 100)
    logger.info(f"Outputs generados en: {run_dir}")

    return run_dir

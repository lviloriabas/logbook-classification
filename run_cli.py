#!/usr/bin/env python3
"""Interfaz de línea de comandos de Logbook Classification.

Por defecto procesa TODOS los PDFs de la carpeta ``input/`` y genera un
único CSV consolidado en ``output/`` con nombre en mayúsculas en el formato
``BITS <DD MON YYYY> <HH MM>.CSV`` (fecha/hora de la corrida).

Todos los outputs de la corrida se generan dentro de una carpeta con el
nombre del CSV (sin extensión): ``output/BITS <DD MON YYYY> <HH MM>/``.
El CSV y el JSON consolidado (mismo nombre que el CSV) van en su
subcarpeta ``datos/``; junto a ellos quedan ``stats.json``, los logs,
los PDFs ordenados (PDFs por matrícula, o carpetas por matrícula con un
PDF por mes dentro) y los informes opcionales.

Opciones útiles:
    --pages 1-40      Procesar solo ese rango de páginas del lote completo,
                      numerado de corrido sobre los PDFs de la entrada.
    --debug           Generar debug.pdf con los bounding boxes de los
                      campos sobre los archivos procesados.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT))

from app.utils.portable import ensure_portable_env

ensure_portable_env()
os.chdir(_ROOT)

from app.core.config import AppConfig, config_for_pdf
from app.core.page_range import PageRange, slice_paths
from app.core.pipeline import OcrProcessPool, Pipeline, process_pdf_batch
from app.core.parallelism import available_cpu_threads, recommended_parallelism
from app.models.schemas import Status
from app.ocr.engine import create_engine
from app.reports.csv_reporter import CsvReporter
from app.reports.json_reporter import JsonReporter
from app.templates.manager import TemplateManager
from app.utils.logging import setup_logging

_MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
           "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="logbook-classification",
        description=(
            "Validates scanned aircraft logbooks against a template. "
            "Processes all PDFs in the input folder by default."
        ),
    )
    parser.add_argument(
        "--pdf", default=None,
        help="PDF específico a procesar (default: todos los PDFs de input/)",
    )
    parser.add_argument(
        "--input-dir", default="input",
        help="Carpeta con los PDFs a analizar (default: input)",
    )
    parser.add_argument(
        "--template",
        default=str(
            Path(__file__).resolve().parent
            / "template/aircraft_log.json"
        ),
        help="Plantilla JSON (default: aircraft_log.json)",
    )
    parser.add_argument(
        "--output-dir", default="output", help="Carpeta de resultados"
    )
    parser.add_argument("--dpi", type=int, default=150, help="DPI de renderizado")
    parser.add_argument(
        "--pages", default=None, metavar="RANGO",
        help="Procesar solo un rango de páginas del lote completo, numerado "
             "de corrido sobre los PDFs ordenados de la entrada: '1-40' "
             "(las 40 primeras del lote, caigan donde caigan), '200-' (desde "
             "la 200 hasta el final) o '15' (solo esa página). Sin esta "
             "opción se procesa todo.",
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="Generar debug.pdf con las páginas originales, sin anotaciones",
    )
    parser.add_argument(
        "--reference-page", type=int, default=1,
        help="Página usada como referencia de alineación (default: 1)",
    )
    parser.add_argument(
        "--threads", "--cpu-threads", dest="cpu_threads", type=int,
        default=None,
        help="Hilos totales del procesador (default: todos los disponibles)",
    )
    parser.add_argument("--no-deskew", action="store_true",
                        help="Desactivar corrección de inclinación")
    parser.add_argument("--no-align", action="store_true",
                        help="Desactivar alineación con plantilla")
    parser.add_argument(
        "--no-remove-printed", action="store_true",
        help="No construir el mapa del fondo impreso idéntico en todas las "
             "páginas. Recomendado dejarlo activado para firmas y ranuras.",
    )
    parser.add_argument(
        "--no-crop-preprocess", action="store_true",
        help="Sin preprocesado de los recortes (escala + localización de "
             "tinta): se envía al OCR el recorte crudo. Útil para comparar "
             "motores con escritura a mano.",
    )
    parser.add_argument(
        "--separar-por", action="append", choices=["avion", "mes"],
        default=None,
        help="Separar los archivos en PDFs independientes. Repetible: "
             "'avion' (un PDF por aeronave), 'mes' (un PDF por mes) o ambos "
             "para separar simultáneamente. Sin esta opción se genera un "
             "único PDF con el mismo nombre que la carpeta de salida.",
    )
    parser.add_argument(
        "--un-solo-pdf", action="store_true",
        help="Generar un único PDF con el mismo nombre que la carpeta de "
             "salida: con --separar-por se "
             "insertan páginas separadoras de matrícula/mes entre los "
             "grupos; sin separadores, un PDF plano sin dividir.",
    )
    parser.add_argument(
        "--discrepancias", action="store_true",
        help="Separar las páginas con firmas faltantes o inciertas. En un "
             "PDF único se agregan al final bajo 'Posibles discrepancias'; "
             "en modo de varios archivos se genera discrepancias.pdf.",
    )
    parser.add_argument(
        "--recortes-firmas", action="store_true",
        help="Volcar los recortes de las regiones de firma a "
             "recortes_firmas/ para auditar visualmente los bounding boxes "
             "(usar con --pages para lotes pequeños).",
    )
    parser.add_argument(
        "--errores", action="store_true",
        help="Generar errores.pdf con las páginas cuyos campos OCR "
             "(matrícula, fechas, log_number) quedaron sin resolver tras "
             "los correctores, sin anotaciones.",
    )
    parser.add_argument("--verbose", action="store_true", help="Logs detallados")
    return parser.parse_args()


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    try:
        return _run(args)
    except Exception as exc:  # noqa: BLE001 - CLI amigable
        print(f"\nERROR: {exc}", file=sys.stderr)
        if args.verbose:
            import traceback

            traceback.print_exc()
        return 1


def _resolve_pdfs(args: argparse.Namespace) -> list[Path]:
    if args.pdf:
        pdf = Path(args.pdf)
        if not pdf.exists():
            raise FileNotFoundError(f"PDF no encontrado: {pdf}")
        return [pdf]
    folder = Path(args.input_dir)
    if not folder.is_dir():
        raise FileNotFoundError(
            f"Carpeta de entrada no encontrada: {folder}"
        )
    pdfs = sorted(
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() == ".pdf"
    )
    if not pdfs:
        raise ValueError(f"No hay archivos PDF en {folder}")
    return pdfs


def _csv_name() -> str:
    now = datetime.now()
    month = _MONTHS[now.month - 1]
    stamp = now.strftime(f"%d {month} %Y %H %M").upper()
    return f"BITS {stamp}.CSV"


def _print_pdf_result(
    pdf_path: Path, report, vlm_stats: dict, pages: PageRange
) -> None:
    tramo = "" if pages.is_full else f" ({pages.label()} del archivo)"
    print(f"\n>>> {pdf_path.name}{tramo}")
    if vlm_stats.get("enabled"):
        print(
            f"  VLM: {vlm_stats.get('crops', 0)} recorte(s), "
            f"{vlm_stats.get('signatures_resolved', 0)} firma(s) y "
            f"{vlm_stats.get('fields_resolved', 0)} campo(s) resueltos"
            + (f" [{vlm_stats.get('model')}]"
               if vlm_stats.get("model") else "")
        )
    summary = report.summary
    print(f"  Resumen {pdf_path.name}: {summary.get('total_pages', 0)} "
          f"páginas | OK: {summary.get('ok_pages', 0)} "
          f"| WARNING: {summary.get('warning_pages', 0)} "
          f"| ERROR: {summary.get('error_pages', 0)} "
          f"| {report.processing_ms / 1000:.2f} s")
    for page in report.pages:
        if page.status is Status.OK:
            continue
        for field in page.fields:
            if field.status is not Status.OK:
                print(f"    P{page.page_number:02d} "
                      f"{field.field_id}: {field.status.value} "
                      f"({field.value!r})")
                break


def _run(args: argparse.Namespace) -> int:
    from loguru import logger

    template = TemplateManager().load(Path(args.template))
    config = AppConfig(
        dpi=args.dpi,
        deskew=not args.no_deskew,
        align=not args.no_align,
        ocr_engine="paddle",
        ocr_lang="en",
        remove_printed=not args.no_remove_printed,
        crop_preprocess=not args.no_crop_preprocess,
        ocr_rec_model="PP-OCRv5_mobile_rec",
        ocr_det_model="PP-OCRv6_medium_det",
        date_engine_name="",
        date_ocr_fallback=False,
        date_slot_ocr=False,
        vlm_enabled=False,
    )

    page_range = PageRange()
    if args.pages is not None:
        try:
            page_range = PageRange.parse(args.pages)
        except ValueError as exc:
            print(f"ERROR: --pages inválido: {exc}", file=sys.stderr)
            return 1
    if args.cpu_threads is not None and args.cpu_threads < 1:
        print("ERROR: --threads debe ser >= 1", file=sys.stderr)
        return 1

    # ── Carpeta de la corrida: nombre del CSV (sin extensión) ──────────
    csv_name = _csv_name()
    run_dir = Path(args.output_dir) / Path(csv_name).stem
    run_dir.mkdir(parents=True, exist_ok=True)
    setup_logging(run_dir / "logs",
                  level="DEBUG" if args.verbose else "INFO")

    entrada = _resolve_pdfs(args)
    # El rango numera el lote de corrido: se reparte entre los archivos que
    # lo contienen y los demás ni se abren.
    slices = slice_paths(entrada, page_range)
    if not slices:
        print(
            f"ERROR: el rango ({page_range.label()}) no incluye ninguna "
            f"página de los {len(entrada)} archivo(s) de la entrada",
            file=sys.stderr,
        )
        return 1
    pdfs = [item.path for item in slices]

    def on_progress(done: int, total: int, message: str) -> None:
        if total:
            pct = int(done * 100 / total)
            print(f"\r[{pct:3d}%] {message:<60}", end="", flush=True)

    print(f"Analizando {len(pdfs)} archivo(s) de: {pdfs[0].parent}")
    if not page_range.is_full:
        print(f"Rango del lote: {page_range.label()}")
    print(f"Plantilla : {template.name} ({len(template.fields)} campos)")
    print(f"Salida    : {run_dir}")

    selected_threads = (
        args.cpu_threads
        if args.cpu_threads is not None
        else available_cpu_threads()
    )
    workers, cpu_threads = recommended_parallelism(selected_threads)
    engine_kwargs = {"cpu_threads": cpu_threads} if cpu_threads else {}
    if config.ocr_rec_model:
        engine_kwargs["rec_model"] = config.ocr_rec_model
    if config.ocr_det_model:
        engine_kwargs["det_model"] = config.ocr_det_model
    engine = create_engine(
        config.ocr_engine, lang=config.ocr_lang, **engine_kwargs
    )
    date_engine = None
    print(
        "Motor OCR fijo: PaddleOCR "
        "PP-OCRv6_medium_det + PP-OCRv5_mobile_rec"
    )
    print(
        f"Hilos seleccionados: {selected_threads} | "
        f"distribución automática: {workers} worker(s) x "
        f"{cpu_threads} hilos c/u"
    )

    reports = []
    vlm_stats = []
    process_pool = (
        OcrProcessPool(
            workers,
            config,
            config.ocr_engine,
            config.ocr_lang,
            cpu_threads,
            None,
        )
        if workers > 1 else None
    )
    if process_pool is not None:
        print(
            f"Perfil C activo: {workers} workers persistentes, "
            f"planificación adaptativa y colas acotadas"
        )
        reports, vlm_stats = process_pdf_batch(
            entrada,
            config,
            template,
            process_pool,
            engine,
            date_engine=date_engine,
            page_range=page_range,
            reference_page=args.reference_page,
            on_progress=on_progress,
        )
        for item, report, stats in zip(slices, reports, vlm_stats):
            _print_pdf_result(item.path, report, stats, item.pages)
    else:
        print("Perfil C activo: ejecución secuencial con un worker")
        for item in slices:
            pdf_path = item.path
            file_config = config_for_pdf(config, pdf_path)
            logger.info(f"[CLI] Procesando: {pdf_path.name}")
            print(
                f"\nProcesando {pdf_path.name}: "
                f"{file_config.dpi} DPI base / "
                f"{file_config.date_dpi} DPI fecha"
            )
            pipeline = Pipeline(
                file_config,
                engine,
                template,
                on_progress=on_progress,
                workers=workers,
                cpu_threads=cpu_threads,
                date_engine=date_engine,
                process_pool=process_pool,
                reference_page=args.reference_page,
            )
            report = pipeline.process(pdf_path, page_range=item.pages)
            reports.append(report)
            vlm_stats.append(pipeline.vlm_stats)
            _print_pdf_result(
                pdf_path, report, pipeline.vlm_stats, item.pages
            )

    if process_pool is not None:
        process_pool.close()

    # ── Corrector de matrículas por libro (un avión por libro) ──────────
    from app.validation.book_corrector import correct_matricula_by_book
    from app.validation.date_corrector import correct_dates_by_book

    stats = correct_matricula_by_book(reports)
    print(f"\nCorrector de matrículas por libro: {stats['books']} libro(s), "
          f"{stats['corrected']} matrícula(s) corregidas, "
          f"{stats['flagged']} discrepante(s) marcada(s)")

    stats = correct_dates_by_book(reports)
    print(f"Corrector de fechas por libro: {stats['books']} libro(s), "
          f"{stats['corrected']} fecha(s) corregidas, "
          f"{stats['flagged']} discrepante(s) marcada(s), "
          f"{stats['regressions']} regresión(es) de fecha")

    # ── Clasificación de discrepancias (para stats y PDFs) ─────────────
    from app.validation.discrepancias import clasificar_lote

    entradas = clasificar_lote(reports, template)
    excluidas = None
    pdf_unico = args.un_solo_pdf or not args.separar_por
    if args.discrepancias and entradas:
        excluidas = {
            (Path(e.pdf_path).name, e.page_number) for e in entradas
        }

    # ── PDF de errores: campos OCR sin resolver (indexación manual) ────
    if args.errores:
        from app.reports.organize import escribir_pdf_errores

        errores_path = escribir_pdf_errores(reports, template, run_dir,
                                            dpi=args.dpi)
        print(f"  Errores (indexación manual): {errores_path}")

    # ── Reportes (carpeta datos/ de la corrida) ─────────────────────────
    corrida = Path(csv_name).stem
    datos_dir = run_dir / "datos"
    csv_path = datos_dir / csv_name
    from app.reports.dual_csv import write_minimal_csv
    from app.reports.outputs import complete_csv_path

    full_csv_path = complete_csv_path(csv_path)
    CsvReporter().write(reports, full_csv_path, template)
    write_minimal_csv(full_csv_path, csv_path)
    json_path = datos_dir / f"{corrida}.json"
    JsonReporter().write_consolidated(reports, json_path, corrida=corrida)

    total_ms = sum(r.processing_ms for r in reports)
    print(f"\nReportes generados:")
    print(f"  CSV mínimo : {csv_path}")
    print(f"  CSV completo: {full_csv_path}")
    print(f"  JSON: {json_path} ({len(reports)} archivo(s))")

    # ── Organización: PDFs por avión/mes y discrepancias ────────────────
    if args.recortes_firmas:
        from app.reports.organize import escribir_recortes_firmas

        recortes_dir = escribir_recortes_firmas(
            reports, template, run_dir, dpi=args.dpi
        )
        print(f"  Recortes de firmas: {recortes_dir}")

    if args.discrepancias and entradas and not pdf_unico:
        from app.reports.organize import escribir_pdf_discrepancias

        discrepancias_path = escribir_pdf_discrepancias(
            entradas, template, run_dir, dpi=args.dpi
        )
        faltantes = sum(1 for e in entradas
                        if e.categoria.value == "missing")
        inciertas = len(entradas) - faltantes
        print(f"  Discrepancias: {discrepancias_path} "
              f"({len(entradas)} página(s): {faltantes} faltante(s), "
              f"{inciertas} para revisar)")
    elif args.discrepancias and not entradas and not pdf_unico:
        print("  Discrepancias: no hay páginas para exportar")

    if pdf_unico:
        from app.reports.organize import escribir_pdf_unico

        pdf_path = escribir_pdf_unico(
            reports, run_dir, args.separar_por or [],
            excluidas, dpi=args.dpi,
            discrepancias_al_final=args.discrepancias,
        )
        print(f"  PDF único: {pdf_path.relative_to(run_dir)}")
    elif args.separar_por:
        from app.reports.organize import generar_pdfs

        pdf_paths = generar_pdfs(
            reports, run_dir, args.separar_por, excluidas, dpi=args.dpi
        )
        print(f"  PDFs separados ({len(pdf_paths)}):")
        for pdf_path in pdf_paths:
            print(f"    - {pdf_path.relative_to(run_dir)}")

    # ── Estadísticas de la corrida (siempre) ────────────────────────────
    from app.reports.stats import escribir_stats

    stats_path = escribir_stats(
        reports, run_dir, corrida=corrida,
        separar_por=args.separar_por, entradas=entradas,
        excluidas=excluidas,
        vlm_stats=vlm_stats,
    )
    print(f"  Stats: {stats_path}")

    if args.debug:
        from app.reports.debug_pdf import write_debug_pdf

        debug_path = run_dir / "debug.pdf"
        write_debug_pdf(reports, template, debug_path, dpi=args.dpi,
                        crop_padding=config.crop_padding)
        print(f"  Debug: {debug_path}")
    print(f"Tiempo total: {total_ms / 1000:.2f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())

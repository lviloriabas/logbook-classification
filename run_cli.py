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
    --limit-books N   Procesar solo los primeros N archivos.
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

from app.core.config import AppConfig
from app.core.pipeline import Pipeline
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
            / "app/templates/examples/aircraft_log.json"
        ),
        help="Plantilla JSON (default: aircraft_log.json)",
    )
    parser.add_argument(
        "--output-dir", default="output", help="Carpeta de resultados"
    )
    parser.add_argument("--dpi", type=int, default=150, help="DPI de renderizado")
    parser.add_argument(
        "--max-pages", type=int, default=None,
        help="Procesar solo las primeras N páginas de cada PDF (para pruebas)",
    )
    parser.add_argument(
        "--limit-books", type=int, default=None,
        help="Procesar solo los primeros N archivos (PDFs ordenados "
             "de la carpeta de entrada)",
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="Generar debug.pdf con los bounding boxes de los campos "
             "sobre los archivos",
    )
    parser.add_argument(
        "--reference-page", type=int, default=1,
        help="Página usada como referencia de alineación (default: 1)",
    )
    parser.add_argument(
        "--engine", default="paddle", choices=["paddle", "tesseract"],
        help="Motor OCR",
    )
    parser.add_argument("--lang", default="en", help="Idioma del motor OCR")
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
        help="No quitar del OCR el fondo impreso idéntico en todas las "
             "páginas (etiquetas, separadores, grilla). Recomendado dejarlo "
             "activado: mejora la lectura de fecha y matrícula.",
    )
    parser.add_argument(
        "--no-crop-preprocess", action="store_true",
        help="Sin preprocesado de los recortes (escala + localización de "
             "tinta): se envía al OCR el recorte crudo. Útil para comparar "
             "motores con escritura a mano.",
    )
    parser.add_argument(
        "--rec-model", default=None,
        help="Modelo de reconocimiento de PaddleOCR (p. ej. "
             "PP-OCRv5_mobile_rec, PP-OCRv6_medium_rec). Autodetecta: "
             "manuscrito si está precargado en portable/paddlex.",
    )
    parser.add_argument(
        "--det-model", default=None,
        help="Modelo de detección de PaddleOCR (p. ej. "
             "PP-OCRv6_medium_det, PP-OCRv6_tiny_det). El medium detecta "
             "manuscrito pequeño que el tiny no capta. Autodetecta: "
             "medium si está precargado en portable/paddlex.",
    )
    parser.add_argument(
        "--no-date-ocr-fallback", action="store_true",
        help="Desactivar la segunda pasada OCR (Tesseract restringido) para "
             "los campos de fecha day/month/year cuando la lectura principal "
             "falla.",
    )
    parser.add_argument(
        "--no-date-slot-ocr", action="store_true",
        help="Desactivar la lectura por ranuras de casilla (OCR por carácter "
             "sobre las celdas que separan las líneas verticales impresas) "
             "para day/month/year.",
    )
    parser.add_argument(
        "--no-vlm", action="store_true",
        help="Desactivar el verificador VLM local (arbitraje de casos "
             "inciertos: firmas 'unclear' y campos críticos vacíos). "
             "Por defecto actúa solo si portable/llama/ tiene el binario y "
             "los modelos GGUF; si no, el pipeline no cambia.",
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
        help="Generar discrepancias.pdf con las páginas que tienen firmas "
             "faltantes o inciertas. Estas páginas NO se incluyen en los "
             "PDFs por avión/mes.",
    )
    parser.add_argument(
        "--recortes-firmas", action="store_true",
        help="Volcar los recortes de las regiones de firma a "
             "recortes_firmas/ para auditar visualmente los bounding boxes "
             "(usar con --max-pages para lotes pequeños).",
    )
    parser.add_argument(
        "--errores", action="store_true",
        help="Generar errores.pdf con las páginas cuyos campos OCR "
             "(matrícula, fechas, log_number) quedaron sin resolver tras "
             "los correctores, para indexación manual.",
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


def _run(args: argparse.Namespace) -> int:
    from loguru import logger

    template = TemplateManager().load(Path(args.template))
    config = AppConfig(
        dpi=args.dpi,
        deskew=not args.no_deskew,
        align=not args.no_align,
        ocr_engine=args.engine,
        ocr_lang=args.lang,
        remove_printed=not args.no_remove_printed,
        crop_preprocess=not args.no_crop_preprocess,
        ocr_rec_model=args.rec_model,
        ocr_det_model=args.det_model,
        date_ocr_fallback=not args.no_date_ocr_fallback,
        date_slot_ocr=not args.no_date_slot_ocr,
        vlm_enabled=not args.no_vlm,
    )

    if args.max_pages is not None and args.max_pages < 1:
        print("ERROR: --max-pages debe ser >= 1", file=sys.stderr)
        return 1
    if args.limit_books is not None and args.limit_books < 1:
        print("ERROR: --limit-books debe ser >= 1", file=sys.stderr)
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

    pdfs = _resolve_pdfs(args)
    if args.limit_books is not None:
        pdfs = pdfs[: args.limit_books]

    def on_progress(done: int, total: int, message: str) -> None:
        if total:
            pct = int(done * 100 / total)
            print(f"\r[{pct:3d}%] {message:<60}", end="", flush=True)

    print(f"Analizando {len(pdfs)} archivo(s) de: {pdfs[0].parent}")
    if args.limit_books is not None:
        print(f"Limite: primeros {args.limit_books} archivos")
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
    print(
        f"Hilos seleccionados: {selected_threads} | "
        f"distribución automática: {workers} worker(s) x "
        f"{cpu_threads} hilos c/u"
    )

    reports = []
    vlm_stats = []
    for pdf_path in pdfs:
        logger.info(f"[CLI] Procesando: {pdf_path.name}")
        print(f"\n>>> {pdf_path.name}"
              + (f" (primeras {args.max_pages} páginas)"
                 if args.max_pages else ""))

        pipeline = Pipeline(config, engine, template, on_progress=on_progress,
                            workers=workers, cpu_threads=cpu_threads)
        if args.reference_page:
            from app.vision.pdf_loader import render_page

            try:
                pipeline.reference_image = render_page(
                    pdf_path, args.reference_page, config.dpi
                )
            except Exception as exc:  # noqa: BLE001 - página inválida
                print(f"ERROR: página de referencia inválida: {exc}",
                      file=sys.stderr)
                return 1

        report = pipeline.process(pdf_path, max_pages=args.max_pages)
        reports.append(report)
        vlm_stats.append(pipeline.vlm_stats)

        if pipeline.vlm_stats.get("enabled"):
            vlm = pipeline.vlm_stats
            print(
                f"  VLM: {vlm.get('crops', 0)} recorte(s), "
                f"{vlm.get('signatures_resolved', 0)} firma(s) y "
                f"{vlm.get('fields_resolved', 0)} campo(s) resueltos"
            )

        summary = report.summary
        print(f"\n  Resumen {pdf_path.name}: {summary.get('total_pages', 0)} "
              f"páginas | OK: {summary.get('ok_pages', 0)} "
              f"| WARNING: {summary.get('warning_pages', 0)} "
              f"| ERROR: {summary.get('error_pages', 0)} "
              f"| {report.processing_ms / 1000:.2f} s")
        for page in report.pages:
            if page.status is not Status.OK:
                for field in page.fields:
                    if field.status is not Status.OK:
                        print(f"    P{page.page_number:02d} "
                              f"{field.field_id}: {field.status.value} "
                              f"({field.value!r})")
                        break

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
    CsvReporter().write(reports, csv_path, template)
    json_path = datos_dir / f"{corrida}.json"
    JsonReporter().write_consolidated(reports, json_path, corrida=corrida)

    total_ms = sum(r.processing_ms for r in reports)
    print(f"\nReportes generados:")
    print(f"  CSV : {csv_path}")
    print(f"  JSON: {json_path} ({len(reports)} archivo(s))")

    # ── Organización: PDFs por avión/mes y discrepancias ────────────────
    if args.recortes_firmas:
        from app.reports.organize import escribir_recortes_firmas

        recortes_dir = escribir_recortes_firmas(
            reports, template, run_dir, dpi=args.dpi
        )
        print(f"  Recortes de firmas: {recortes_dir}")

    if args.discrepancias:
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

    if args.un_solo_pdf:
        from app.reports.organize import escribir_pdf_unico

        pdf_path = escribir_pdf_unico(
            reports, run_dir, args.separar_por or [],
            excluidas, dpi=args.dpi,
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
    else:
        from app.reports.organize import escribir_pdf_unico

        pdf_path = escribir_pdf_unico(
            reports, run_dir, [], excluidas, dpi=args.dpi
        )
        print(f"  PDF único: {pdf_path.relative_to(run_dir)}")

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

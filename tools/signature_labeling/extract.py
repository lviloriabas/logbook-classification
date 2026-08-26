#!/usr/bin/env python3
"""Extrae los recortes de firma de los PDF para etiquetarlos a mano.

Los recortes tienen que ser *los mismos* que ve el detector, o la calibración
mediría otra cosa: por eso este script no reimplementa la geometría, sino que
repite la del pipeline (render al DPI del documento, corrección de
inclinación, alineación con las anclas estabilizadas del batch) y luego recorta
con un margen más ancho que el del detector, dejando anotado en el manifiesto
dónde queda el campo exacto dentro del PNG.

Uso típico (intérprete portable del proyecto)::

    portable/python312/tools/python.exe tools/signature_labeling/extract.py \
        --pages 1-60 --cada 3

    portable/python312/tools/python.exe tools/signature_labeling/extract.py \
        --input input/test2.pdf --max-por-pdf 80

Vuelve a ejecutarse sin miedo: las muestras ya extraídas se conservan (y con
ellas sus etiquetas), solo se añaden las nuevas.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.signature_labeling.dataset import (  # noqa: E402
    CROPS_DIR,
    EXTRACT_PAD_X,
    EXTRACT_PAD_Y,
    Dataset,
    Sample,
    pad_pixels,
    sample_id,
)

DEFAULT_OUT = ROOT / "output" / "firmas_dataset"
DEFAULT_TEMPLATE = ROOT / "template" / "aircraft_log.json"


def _pdf_list(entries: Sequence[str]) -> List[Path]:
    """PDFs a procesar: archivos sueltos o el contenido de una carpeta."""
    paths: List[Path] = []
    for entry in entries:
        path = Path(entry)
        if not path.is_absolute():
            path = ROOT / path
        if path.is_dir():
            paths.extend(sorted(path.glob("*.pdf")))
        elif path.is_file():
            paths.append(path)
        else:
            print(f"  aviso: no existe {path}", flush=True)
    return paths


def _selected_pages(first: int, last: int, every: int, cap: Optional[int]) -> List[int]:
    """Páginas a extraer dentro del tramo, repartidas por todo el documento.

    El muestreo es por paso fijo y no aleatorio: un batch etiquetado tiene que
    poder reproducirse, y recorrer el documento de punta a punta trae más
    variedad de escaneo que concentrarse en las primeras páginas.
    """
    pages = list(range(first, last + 1, max(1, every)))
    if cap is not None and len(pages) > cap:
        step = len(pages) / cap
        pages = [pages[int(index * step)] for index in range(cap)]
    return pages


def _field_rect_in_crop(field, image_shape, pad_x: float, pad_y: float):
    """Rectángulo del campo dentro del recorte que devuelve ``crop_region``."""
    height, width = image_shape[:2]
    left, top, right, bottom = field.rect_pixels(width, height)
    px = pad_pixels(pad_x, right - left)
    py = pad_pixels(pad_y, bottom - top)
    crop_left = max(0, left - px)
    crop_top = max(0, top - py)
    return [left - crop_left, top - crop_top,
            (left - crop_left) + (right - left),
            (top - crop_top) + (bottom - top)]


def extract(
    pdf_paths: Sequence[Path],
    out_dir: Path,
    template_path: Path,
    *,
    pages_text: Optional[str],
    every: int,
    max_per_pdf: Optional[int],
    field_ids: Optional[Sequence[str]],
    reset: bool,
) -> int:
    import cv2
    from loguru import logger

    from app.core.config import AppConfig, config_for_pdf
    from app.core.page_range import PageRange
    from app.core.pipeline import Pipeline
    from app.templates.manager import TemplateManager
    from app.templates.schema import FieldType
    from app.utils.portable import ensure_portable_env
    from app.vision.alignment import apply_transform
    from app.vision.blank_detection import is_blank
    from app.vision.pdf_loader import PdfPageRenderer
    from app.vision.preprocessing import crop_region, deskew

    ensure_portable_env()
    logger.remove()

    template = TemplateManager().load(template_path)
    signature_fields = [
        field for field in template.fields
        if field.type is FieldType.SIGNATURE
        and (not field_ids or field.id in field_ids)
    ]
    if not signature_fields:
        print("La plantilla no tiene campos de firma que extraer.")
        return 1

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / CROPS_DIR).mkdir(exist_ok=True)
    try:
        dataset = Dataset.load(out_dir)
        if reset:
            dataset = Dataset(root=out_dir)
    except FileNotFoundError:
        dataset = Dataset(root=out_dir)
    known = {sample.id for sample in dataset.samples}
    added = 0

    # Sin OCR y sin máscara de fondo impreso: de todo el pipeline aquí solo
    # interesa la geometría (deskew + alineación), que es lo que decide qué
    # píxeles caen dentro del campo.
    base_config = AppConfig(remove_printed=False, date_slot_ocr=False)
    selection = PageRange.parse(pages_text) if pages_text else PageRange()

    for pdf_path in pdf_paths:
        config = config_for_pdf(base_config, pdf_path)
        started = time.perf_counter()
        with PdfPageRenderer(pdf_path) as renderer:
            count = renderer.page_count()
            first, last = selection.clamped(count)
            if last < first:
                print(f"{pdf_path.name}: el rango no cubre ninguna página")
                continue
            total = last - first + 1
            wanted = _selected_pages(first, last, every, max_per_pdf)
            print(
                f"\n{pdf_path.name}: {count} páginas, "
                f"tramo {first}-{last}, {len(wanted)} a extraer "
                f"(dpi {config.dpi})",
                flush=True,
            )

            reference = renderer.render_page(first, config.dpi)
            pipeline = Pipeline(config, engine=None, template=template)
            print("  calibrando alineación…", end="", flush=True)
            _own, anchors = pipeline._calibrate(  # noqa: SLF001 - misma geometría
                pdf_path, first, total, reference, renderer=renderer,
            )
            print(f" {time.perf_counter() - started:.0f}s", flush=True)

            for position, page_number in enumerate(wanted, start=1):
                image = renderer.render_page(page_number, config.dpi)
                if is_blank(image, config.blank_threshold):
                    print(f"  página {page_number}: en blanco, se omite")
                    continue
                if config.deskew:
                    image, _angle = deskew(image)
                alignment = "sin_alinear"
                if anchors:
                    anchor = anchors[page_number - first]
                    alignment = "ok" if anchor.reliable else "low"
                    if anchor.reliable:
                        image = apply_transform(image, anchor)

                for field in signature_fields:
                    identifier = sample_id(pdf_path.name, page_number, field.id)
                    if identifier in known:
                        continue
                    try:
                        crop = crop_region(
                            image, field,
                            pad_x=EXTRACT_PAD_X, pad_y=EXTRACT_PAD_Y,
                        )
                    except ValueError:
                        continue
                    relative = f"{CROPS_DIR}/{identifier}.png"
                    cv2.imwrite(str(out_dir / relative), crop)
                    dataset.samples.append(Sample(
                        id=identifier,
                        pdf=pdf_path.name,
                        page=page_number,
                        field_id=field.id,
                        file=relative,
                        dpi=config.dpi,
                        alignment=alignment,
                        rect=_field_rect_in_crop(
                            field, image.shape, EXTRACT_PAD_X, EXTRACT_PAD_Y,
                        ),
                    ))
                    known.add(identifier)
                    added += 1
                if position % 10 == 0 or position == len(wanted):
                    print(
                        f"  {position}/{len(wanted)} páginas "
                        f"({added} recortes)",
                        flush=True,
                    )
        dataset.save_manifest()

    dataset.save_manifest()
    print(
        f"\n{added} recortes nuevos; {len(dataset.samples)} en total en "
        f"{out_dir}"
    )
    print("Siguiente paso:\n"
          f"  portable/python312/tools/python.exe "
          f"tools/signature_labeling/label_gui.py --dir {out_dir}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extrae recortes de campos de firma para etiquetarlos",
    )
    parser.add_argument(
        "--input", nargs="+", default=["input"],
        help="PDFs o carpetas con PDFs (por defecto: input/)",
    )
    parser.add_argument(
        "--dir", type=Path, default=DEFAULT_OUT,
        help="carpeta de trabajo del conjunto etiquetado",
    )
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument(
        "--pages", help="tramo de páginas de cada PDF: 1-60, 20-, 15",
    )
    parser.add_argument(
        "--cada", type=int, default=1, dest="every",
        help="extraer una de cada N páginas del tramo (por defecto todas)",
    )
    parser.add_argument(
        "--max-por-pdf", type=int, dest="max_per_pdf",
        help="tope de páginas por PDF, repartidas por todo el tramo",
    )
    parser.add_argument(
        "--campos", nargs="+", dest="field_ids",
        help="limitar a estos campos de firma (por defecto, todos)",
    )
    parser.add_argument(
        "--reset", action="store_true",
        help="rehacer el manifiesto desde cero (las etiquetas se conservan)",
    )
    args = parser.parse_args()

    pdf_paths = _pdf_list(args.input)
    if not pdf_paths:
        print("No se encontró ningún PDF.", file=sys.stderr)
        return 1
    try:
        return extract(
            pdf_paths, args.dir, args.template,
            pages_text=args.pages,
            every=args.every,
            max_per_pdf=args.max_per_pdf,
            field_ids=args.field_ids,
            reset=args.reset,
        )
    except KeyboardInterrupt:
        print("\nInterrumpido; lo extraído hasta ahora queda guardado.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

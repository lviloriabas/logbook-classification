"""Evalúa el lector de fechas contra las imágenes etiquetadas de ``img/``.

El script usa exclusivamente el Python y los modelos portables del proyecto.
No escribe reportes CSV ni modifica las imágenes. Ejemplos::

    portable/python312/tools/python.exe tools/evaluate_date_images.py --run
    portable/python312/tools/python.exe tools/evaluate_date_images.py \
        --predictions tmp/predictions.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

GROUND_TRUTH = ROOT / "tests" / "fixtures" / "date_images_ground_truth.json"
DATE_FIELD_IDS = {
    "day", "day_1", "day_2",
    "month", "month_1", "month_2", "month_3",
    "year", "year_1", "year_2",
}


def _load_truth(path: Path) -> dict[str, dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {sample["file"]: sample for sample in payload["samples"]}


def _parts(value: Optional[str]) -> tuple[Optional[str], ...]:
    if not value:
        return None, None, None
    pieces = value.split("/")
    return tuple(pieces) if len(pieces) == 3 else (None, None, None)


def evaluate(truth: dict[str, dict], predictions: dict[str, Optional[str]]) -> dict:
    counters = Counter(total=len(truth))
    details = []
    for name, sample in truth.items():
        expected = sample["date"]
        actual = predictions.get(name)
        if actual == expected:
            counters["date_exact"] += 1
        if actual:
            counters["date_detected"] += 1
        expected_parts = _parts(expected)
        actual_parts = _parts(actual)
        for label, exp, got in zip(("year", "month", "day"), expected_parts, actual_parts):
            counters[f"{label}_exact"] += int(exp == got)
        details.append({
            "file": name,
            "log_number": sample["log_number"],
            "expected": expected,
            "actual": actual,
            "correct": actual == expected,
        })
    return {"counts": dict(counters), "details": details}


def _run_reader(truth: dict[str, dict]) -> dict[str, Optional[str]]:
    import cv2
    from loguru import logger

    from app.core.config import AppConfig
    from app.core.pipeline import process_page_image
    from app.ocr.engine import create_engine
    from app.templates.manager import TemplateManager
    from app.utils.portable import ensure_portable_env

    ensure_portable_env()
    logger.remove()
    template = TemplateManager().load(ROOT / "template" / "aircraft_log.json")
    template.fields = [field for field in template.fields if field.id in DATE_FIELD_IDS]
    config = AppConfig(
        dpi=200,
        date_dpi=200,
        deskew=False,
        align=False,
        remove_printed=False,
        vlm_enabled=False,
    )
    engine = create_engine("paddle", lang="en", cpu_threads=4)
    predictions: dict[str, Optional[str]] = {}
    for page_number, name in enumerate(truth, start=1):
        image = cv2.imread(str(ROOT / "img" / name))
        if image is None:
            predictions[name] = None
            continue
        page = process_page_image(
            image, page_number, config, engine, template, reference=None,
        )
        predictions[name] = page.date
        print(f"{name}: {page.date or 'SIN FECHA'}", flush=True)
    return predictions


def _print_report(report: dict) -> None:
    counts = report["counts"]
    total = counts["total"]
    print("\nResultado")
    for key in ("date_detected", "date_exact", "day_exact", "month_exact", "year_exact"):
        count = counts.get(key, 0)
        print(f"  {key:14}: {count:>3}/{total} ({count / total:6.1%})")
    print("\nErrores")
    for detail in report["details"]:
        if not detail["correct"]:
            print(
                f"  {detail['file']}: esperado={detail['expected']} "
                f"obtenido={detail['actual']}"
            )


def main() -> int:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--run", action="store_true", help="ejecutar el lector actual")
    source.add_argument("--predictions", type=Path, help="JSON {archivo: fecha|null}")
    parser.add_argument("--truth", type=Path, default=GROUND_TRUTH)
    parser.add_argument("--save", type=Path, help="guardar predicciones JSON")
    args = parser.parse_args()

    truth = _load_truth(args.truth)
    predictions = (
        _run_reader(truth)
        if args.run
        else json.loads(args.predictions.read_text(encoding="utf-8"))
    )
    if args.save:
        args.save.parent.mkdir(parents=True, exist_ok=True)
        args.save.write_text(
            json.dumps(predictions, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    _print_report(evaluate(truth, predictions))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

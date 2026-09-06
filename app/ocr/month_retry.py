"""Relectura acotada del mes y de las casillas que separan candidatos."""

from __future__ import annotations

from loguru import logger

from app.models.schemas import Status
from app.ocr.month_evidence import resolve_month_cells
from app.ocr.regional import ocr_regions
from app.utils.postprocess import apply_postprocess


def retry_month(page, image, template, engine, *, date_engine=None,
                date_image=None, date_region=None, overrides=None, dpi=None):
    """Relee como máximo cuatro recortes originales, sin sumar votos."""
    fields = {field.field_id: field for field in page.fields}
    month = fields.get("month")
    if month is None or (month.value and month.status is not Status.ERROR
                         and month.confidence >= 0.35):
        return
    target_engine = date_engine if date_engine is not None else engine
    if not (hasattr(target_engine, "recognize_lines")
            or hasattr(target_engine, "recognize_batch")):
        return
    cells = [fields.get(f"month_{i}") for i in range(1, 4)]
    if any(cell is None for cell in cells):
        return
    options = month.alternatives
    decisive = [i for i in range(3)
                if len({word[i] for word in options if len(word) == 3}) > 1]
    if not decisive:
        decisive = [i for i, cell in enumerate(cells)
                    if not cell.value or cell.confidence < 0.35]
    ids = ["month", *(f"month_{i + 1}" for i in decisive)]
    selected = [template.field(fid) for fid in ids]
    if any(field is None for field in selected):
        return
    wider = dict(overrides or {})
    for field in selected:
        effective = wider.get(field.id, field)
        # Un margen pequeño recupera trazos pegados al borde. Conserva la
        # geometría ajustada y nunca sale de la página o de la banda.
        left = max(0.0, effective.x - effective.w * 0.04)
        top = max(0.0, effective.y - effective.h * 0.04)
        right = min(1.0, effective.x + effective.w * 1.04)
        bottom = min(1.0, effective.y + effective.h * 1.04)
        wider[field.id] = effective.model_copy(update={
            "x": left, "y": top, "w": right - left, "h": bottom - top,
        })
    try:
        readings = ocr_regions(
            engine, image, selected, preprocess=False, dpi=dpi,
            date_engine=date_engine, date_image=date_image,
            date_region=date_region, overrides=wider,
        )
    except Exception as exc:
        logger.debug(f"No se pudo releer el mes: {exc}")
        return
    trial = month.model_copy(deep=True)
    trial_cells = [cell.model_copy(deep=True) for cell in cells]
    for fid, (raw, confidence) in zip(ids, readings):
        if fid == "month":
            value, note = apply_postprocess(fid, "month", raw)
            trial.raw_value = raw
            trial.value = value or None
            trial.confidence = confidence
            trial.status = Status.OK if value and not note and confidence >= 0.5 else Status.ERROR
        else:
            cell = trial_cells[int(fid[-1]) - 1]
            cell.raw_value = raw
            cell.value = raw or None
            cell.confidence = confidence
    resolve_month_cells(trial, trial_cells)
    if (not trial.value or trial.status is Status.ERROR or trial.confidence < 0.5
            or (options and trial.value not in options)):
        return
    month.value = trial.value
    month.confidence = trial.confidence
    month.alternatives = [word for word in options if word != trial.value]
    month.status = Status.WARNING if month.alternatives else Status.OK
    month.source = "ocr_fallback"
    month.inference_method = "month_retry"
    month.comment = "Mes recuperado al releer la palabra y sus letras dudosas"

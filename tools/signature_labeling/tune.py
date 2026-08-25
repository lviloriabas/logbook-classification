#!/usr/bin/env python3
"""Ajusta los umbrales del detector de firmas con las etiquetas humanas.

Qué hace, en orden:

1. Mide cada recorte etiquetado con la función real del detector, una vez por
   cada combinación de margen y umbral de tinta (``pad_x``, ``pad_y``,
   ``ink_delta``). Es la parte cara, y queda cacheada en ``features.json``.
2. Recorre las combinaciones de los tres umbrales de decisión
   (``min_ink_peak``, ``max_empty_peak``, ``min_ink_span``) sobre esos rasgos
   ya medidos y se queda con la de menor coste.
3. Comprueba con validación cruzada que la mejora no es memoria del conjunto:
   busca en cuatro quintas partes y mide en la quinta que no vio.
4. Informa, y con ``--aplicar`` escribe los umbrales en la plantilla.

Uso::

    portable/python312/tools/python.exe tools/signature_labeling/tune.py
    portable/python312/tools/python.exe tools/signature_labeling/tune.py --aplicar

El coste no es la tasa de acierto: dar por firmada una página sin firmar
esconde una falta real y pesa 6, reclamar una firma que sí está pesa 4, y
dejarlo en incierto pesa 1 porque solo cuesta una revisión (ver
``evaluate.py``).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.utils.portable import ensure_portable_env  # noqa: E402

ensure_portable_env()

import numpy as np  # noqa: E402

from tools.signature_labeling.dataset import (  # noqa: E402
    LABEL_ABSENT,
    LABEL_PRESENT,
    Dataset,
    EXTRACT_PAD_X,
    EXTRACT_PAD_Y,
    Sample,
)
from tools.signature_labeling.evaluate import (  # noqa: E402
    COST_FALSE_ABSENT,
    COST_FALSE_PRESENT,
    COST_UNCLEAR,
    DEFAULT_TEMPLATE,
    Score,
    Thresholds,
    decision_masks,
    load_template,
    measure_samples,
    signature_fields,
)

DEFAULT_DIR = ROOT / "output" / "firmas_dataset"
FEATURE_CACHE = "features.json"

# Rejilla de búsqueda. Los márgenes no pueden pasar de los de extracción
# (dataset.EXTRACT_PAD_*): más allá no hay píxeles guardados.
PAD_X_GRID = (0.0, 0.05, 0.10)
PAD_Y_GRID = (0.0, 0.10, 0.20, 0.30)
DELTA_GRID = (50.0, 60.0, 70.0, 80.0, 90.0, 100.0)
PEAK_GRID = tuple(round(0.03 + 0.01 * step, 3) for step in range(28))
EMPTY_GRID = tuple(round(0.01 + 0.01 * step, 3) for step in range(15))
# 1.0 es el tope que admite la plantilla y en la práctica apaga la regla de la
# escritura repartida: exige tinta en *todas* las columnas del recorte.
SPAN_GRID = (0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.60, 0.70, 1.0)

FAST_PAD_X_GRID = (0.0, 0.05)
FAST_PAD_Y_GRID = (0.0, 0.10, 0.20)
FAST_DELTA_GRID = (60.0, 80.0, 100.0)


class Weights:
    """Pesos del coste, configurables desde la línea de órdenes."""

    def __init__(self, false_present: float, false_absent: float, unclear: float):
        self.false_present = false_present
        self.false_absent = false_absent
        self.unclear = unclear


def score_from_masks(
    is_true: np.ndarray,
    is_false: np.ndarray,
    present: np.ndarray,
    absent: np.ndarray,
) -> Score:
    """Cuenta aciertos y errores sin recorrer las muestras una a una."""
    unclear = ~is_true & ~is_false
    return Score(
        total=int(present.sum() + absent.sum()),
        correct=int((present & is_true).sum() + (absent & is_false).sum()),
        false_present=int((absent & is_true).sum()),
        false_absent=int((present & is_false).sum()),
        unclear_present=int((present & unclear).sum()),
        unclear_absent=int((absent & unclear).sum()),
    )


def cost_of(score: Score, weights: Weights) -> float:
    return score.cost(weights.false_present, weights.false_absent, weights.unclear)


class FeatureStore:
    """Rasgos medidos por combinación de margen y umbral de tinta.

    La medición es lo único caro de la calibración (morfología sobre cada
    recorte), así que se guarda en disco: repetir la búsqueda con otros pesos
    o con otra rejilla de umbrales es entonces instantáneo.
    """

    def __init__(self, dataset: Dataset, fields: Dict, cache_path: Path):
        self.dataset = dataset
        self.fields = fields
        self.cache_path = cache_path
        self.cache: Dict[str, Dict[str, List[float]]] = {}
        if cache_path.is_file():
            try:
                self.cache = json.loads(cache_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                self.cache = {}
        self._dirty = False

    @staticmethod
    def key(pad_x: float, pad_y: float, delta: float) -> str:
        return f"px{pad_x:.2f}_py{pad_y:.2f}_d{delta:.0f}"

    def features(
        self, samples: Sequence[Sample], pad_x: float, pad_y: float, delta: float
    ) -> np.ndarray:
        key = self.key(pad_x, pad_y, delta)
        stored = self.cache.setdefault(key, {})
        missing = [s for s in samples if s.id not in stored]
        if missing:
            rows = measure_samples(
                self.dataset, missing, self.fields, pad_x, pad_y, delta
            )
            for sample, row in zip(missing, rows):
                stored[sample.id] = [round(float(value), 6) for value in row]
            self._dirty = True
        return np.array(
            [stored[sample.id] for sample in samples], dtype=np.float64
        )

    def flush(self) -> None:
        if not self._dirty:
            return
        self.cache_path.write_text(
            json.dumps(self.cache), encoding="utf-8",
        )
        self._dirty = False


def search(
    features: np.ndarray,
    present: np.ndarray,
    absent: np.ndarray,
    base: Thresholds,
    weights: Weights,
    min_resolved: float = 0.80,
    peaks: Sequence[float] = PEAK_GRID,
    empties: Sequence[float] = EMPTY_GRID,
    spans: Sequence[float] = SPAN_GRID,
) -> Tuple[Thresholds, Score, float]:
    """Mejor combinación de los tres umbrales de decisión para esos rasgos.

    ``min_resolved`` es lo que impide la solución tramposa: como dejar algo en
    incierto cuesta mucho menos que equivocarse, el óptimo sin restricción es
    un detector que no se moja con nada y manda el batch entero a revisión
    manual. Se descartan las configuraciones que resuelven menos de esa
    fracción de los recortes etiquetados.
    """
    best: Optional[Tuple[Thresholds, Score, float]] = None
    fallback: Optional[Tuple[Thresholds, Score, float]] = None
    for peak_high in peaks:
        for peak_low in empties:
            if peak_low > peak_high:
                continue  # la plantilla no admite umbrales invertidos
            for span in spans:
                candidate = Thresholds(
                    pad_x=base.pad_x, pad_y=base.pad_y,
                    ink_delta=base.ink_delta,
                    min_ink_peak=peak_high,
                    max_empty_peak=peak_low,
                    min_ink_span=span,
                    max_ink_ratio=base.max_ink_ratio,
                )
                is_true, is_false = decision_masks(features, candidate)
                score = score_from_masks(is_true, is_false, present, absent)
                cost = cost_of(score, weights)
                if fallback is None or cost < fallback[2] - 1e-12:
                    fallback = (candidate, score, cost)
                resolved = (
                    (score.total - score.unclear) / score.total
                    if score.total else 0.0
                )
                if resolved < min_resolved:
                    continue
                if best is None or cost < best[2] - 1e-12:
                    best = (candidate, score, cost)
    assert fallback is not None
    return best if best is not None else fallback


def folds(labels: Sequence[str], field_ids: Sequence[str], count: int) -> List[np.ndarray]:
    """Particiones estratificadas por campo y etiqueta.

    Sin estratificar, una partición podía quedarse sin ausencias de un campo y
    el resultado dependería del sorteo en lugar de los datos.
    """
    groups: Dict[Tuple[str, str], List[int]] = {}
    for index, (label, field_id) in enumerate(zip(labels, field_ids)):
        groups.setdefault((field_id, label), []).append(index)
    buckets: List[List[int]] = [[] for _ in range(count)]
    for key in sorted(groups):
        for position, index in enumerate(groups[key]):
            buckets[position % count].append(index)
    return [np.array(sorted(bucket), dtype=int) for bucket in buckets]


def _print_score(title: str, score: Score, cost: float) -> None:
    print(f"  {title:<26} {score.line()}  coste {cost:.3f}")


def _report_errors(
    dataset: Dataset,
    samples: Sequence[Sample],
    labels: Sequence[str],
    features: np.ndarray,
    thresholds: Thresholds,
    limit: int,
) -> None:
    is_true, is_false = decision_masks(features, thresholds)
    rows: List[Tuple[str, Sample]] = []
    for index, sample in enumerate(samples):
        expected_present = labels[index] == LABEL_PRESENT
        if expected_present and is_false[index]:
            rows.append(("FALSO AUSENTE ", sample))
        elif not expected_present and is_true[index]:
            rows.append(("FALSO PRESENTE", sample))
    if not rows:
        print("  sin errores graves con la configuración propuesta")
        return
    print(f"  {len(rows)} errores graves; los primeros {min(limit, len(rows))}:")
    for kind, sample in rows[:limit]:
        print(f"    {kind}  {dataset.crop_path(sample)}")


def _apply_to_template(
    template_path: Path,
    thresholds: Thresholds,
    per_field: Optional[Dict[str, Thresholds]] = None,
) -> None:
    payload = json.loads(template_path.read_text(encoding="utf-8"))
    changed = []
    for field in payload.get("fields", []):
        if field.get("type") != "signature":
            continue
        chosen = (per_field or {}).get(field["id"], thresholds)
        field["ink_delta"] = float(chosen.ink_delta)
        field["min_ink_peak"] = float(chosen.min_ink_peak)
        field["max_empty_peak"] = float(chosen.max_empty_peak)
        field["min_ink_span"] = float(chosen.min_ink_span)
        changed.append(field["id"])
    template_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"\nPlantilla actualizada ({', '.join(changed)}).")


def run(args: argparse.Namespace) -> int:
    dataset = Dataset.load(args.dir)
    template = load_template(args.template)
    fields = signature_fields(template)
    weights = Weights(args.coste_falso_presente, args.coste_falso_ausente,
                      args.coste_incierto)

    pairs = dataset.labeled()
    if args.campos:
        pairs = [(s, l) for s, l in pairs if s.field_id in args.campos]
    if args.solo_alineadas:
        pairs = [(s, l) for s, l in pairs if s.alignment == "ok"]
    if not pairs:
        print("No hay recortes etiquetados todavía. Use label_gui.py.",
              file=sys.stderr)
        return 1

    samples = [sample for sample, _ in pairs]
    labels = [label for _, label in pairs]
    present = np.array([label == LABEL_PRESENT for label in labels])
    absent = np.array([label == LABEL_ABSENT for label in labels])
    field_ids = [sample.field_id for sample in samples]

    counts = dataset.counts()
    print(
        f"Conjunto: {len(samples)} recortes etiquetados "
        f"(firma {int(present.sum())}, ausente {int(absent.sum())}) "
        f"de {len(set(field_ids))} campos; "
        f"{counts['dudosa']} dudosos excluidos"
    )

    store = FeatureStore(dataset, fields, args.dir / FEATURE_CACHE)
    # Configuración actual, medida sobre este mismo conjunto: es la vara.
    reference_field = fields[samples[0].field_id]
    current = Thresholds.from_field(reference_field)

    # Los recortes guardados solo tienen píxeles hasta el margen de
    # extracción: pedir más allá devolvería el mismo recorte con otro nombre.
    # El margen actual entra siempre en la rejilla, aunque no sea uno de los
    # valores redondos: es el único que se puede aplicar sin tocar el código.
    pad_x_grid = sorted({
        pad for pad in (FAST_PAD_X_GRID if args.rapido else PAD_X_GRID)
        if pad <= EXTRACT_PAD_X
    } | {current.pad_x})
    pad_y_grid = sorted({
        pad for pad in (FAST_PAD_Y_GRID if args.rapido else PAD_Y_GRID)
        if pad <= EXTRACT_PAD_Y
    } | {current.pad_y})
    delta_grid = sorted(set(FAST_DELTA_GRID if args.rapido else DELTA_GRID)
                        | {current.ink_delta})

    current_features = store.features(
        samples, current.pad_x, current.pad_y, current.ink_delta
    )
    current_score = score_from_masks(
        *decision_masks(current_features, current), present, absent
    )
    current_cost = cost_of(current_score, weights)

    groups: Dict[Tuple[float, float, float], np.ndarray] = {}
    total_groups = len(pad_x_grid) * len(pad_y_grid) * len(delta_grid)
    started = time.perf_counter()
    print(f"\nMidiendo {total_groups} combinaciones de margen y tinta…")
    for position, pad_x in enumerate(pad_x_grid):
        for pad_y in pad_y_grid:
            for delta in delta_grid:
                groups[(pad_x, pad_y, delta)] = store.features(
                    samples, pad_x, pad_y, delta
                )
        store.flush()
        print(f"  {(position + 1) * len(pad_y_grid) * len(delta_grid)}"
              f"/{total_groups} ({time.perf_counter() - started:.0f}s)",
              flush=True)
    store.flush()

    print("Buscando umbrales…", flush=True)
    # Se empieza por el margen actual y por el umbral de tinta más cercano al
    # de hoy, y solo se cambia de configuración ante una mejora estricta: un
    # empate no debe mandar a editar constantes del código a cambio de nada.
    ordered = sorted(
        groups.items(),
        key=lambda item: (
            (item[0][0], item[0][1]) != (current.pad_x, current.pad_y),
            abs(item[0][2] - current.ink_delta),
        ),
    )
    best: Optional[Tuple[Thresholds, Score, float]] = None
    for (pad_x, pad_y, delta), features in ordered:
        base = Thresholds(pad_x=pad_x, pad_y=pad_y, ink_delta=delta,
                          max_ink_ratio=current.max_ink_ratio)
        candidate = search(features, present, absent, base, weights,
                           args.min_resueltos)
        if best is None or candidate[2] < best[2] - 1e-12:
            best = candidate
    assert best is not None
    proposed, proposed_score, proposed_cost = best
    proposed_features = groups[
        (proposed.pad_x, proposed.pad_y, proposed.ink_delta)
    ]

    # La mejor configuración puede pedir otro margen de recorte, y el margen no
    # vive en la plantilla sino en dos constantes de app/vision/signature.py.
    # Escribir solo los umbrales dejaría una combinación que nadie ha medido,
    # así que se busca también la mejor *con el margen actual*: esa es la única
    # que se puede aplicar sin tocar el código.
    applicable = best
    if (proposed.pad_x, proposed.pad_y) != (current.pad_x, current.pad_y):
        applicable = None
        for (pad_x, pad_y, delta), features in groups.items():
            if (pad_x, pad_y) != (current.pad_x, current.pad_y):
                continue
            base = Thresholds(pad_x=pad_x, pad_y=pad_y, ink_delta=delta,
                              max_ink_ratio=current.max_ink_ratio)
            candidate = search(features, present, absent, base, weights,
                               args.min_resueltos)
            if applicable is None or candidate[2] < applicable[2] - 1e-12:
                applicable = candidate
        assert applicable is not None  # el margen actual está en la rejilla

    # De aquí en adelante el informe habla de lo que se puede aplicar hoy: si
    # el ganador exige otro margen, queda como referencia de cuánto se gana
    # editando las constantes, pero el desglose y la validación son de la otra.
    chosen, chosen_score, chosen_cost = applicable
    chosen_features = groups[
        (chosen.pad_x, chosen.pad_y, chosen.ink_delta)
    ]
    same_pad = (chosen.pad_x, chosen.pad_y)

    print("\nResultado sobre todo el conjunto")
    _print_score("configuración actual", current_score, current_cost)
    if applicable is not best:
        _print_score("mejor con margen actual", chosen_score, chosen_cost)
        _print_score("mejor con otro margen", proposed_score, proposed_cost)
    else:
        _print_score("configuración propuesta", proposed_score, proposed_cost)
    print(f"\n  actual   : {current.describe()}")
    print(f"  propuesta: {chosen.describe()}")
    if applicable is not best:
        print(f"  con otro margen: {proposed.describe()}")

    if args.cv >= 2 and len(samples) >= args.cv * 4:
        print(f"\nValidación cruzada ({args.cv} particiones, se busca en el "
              f"resto y se mide en la que no se vio)")
        partitions = folds(labels, field_ids, args.cv)
        current_costs, tuned_costs = [], []
        for index, test_index in enumerate(partitions, start=1):
            mask = np.zeros(len(samples), dtype=bool)
            mask[test_index] = True
            fold_best: Optional[Tuple[Thresholds, Score, float]] = None
            for (pad_x, pad_y, delta), features in groups.items():
                if (pad_x, pad_y) != same_pad:
                    continue  # se valida el margen que se va a aplicar
                base = Thresholds(pad_x=pad_x, pad_y=pad_y, ink_delta=delta,
                                  max_ink_ratio=current.max_ink_ratio)
                candidate = search(
                    features[~mask], present[~mask], absent[~mask],
                    base, weights, args.min_resueltos,
                )
                if fold_best is None or candidate[2] < fold_best[2] - 1e-12:
                    fold_best = candidate
            assert fold_best is not None
            fold_thresholds = fold_best[0]
            fold_features = groups[(
                fold_thresholds.pad_x, fold_thresholds.pad_y,
                fold_thresholds.ink_delta,
            )]
            test_score = score_from_masks(
                *decision_masks(fold_features[mask], fold_thresholds),
                present[mask], absent[mask],
            )
            test_current = score_from_masks(
                *decision_masks(current_features[mask], current),
                present[mask], absent[mask],
            )
            current_costs.append(cost_of(test_current, weights))
            tuned_costs.append(cost_of(test_score, weights))
            print(f"  partición {index}: actual {current_costs[-1]:.3f} -> "
                  f"calibrada {tuned_costs[-1]:.3f}   "
                  f"({fold_thresholds.describe()})")
        print(f"  media      : actual {np.mean(current_costs):.3f} -> "
              f"calibrada {np.mean(tuned_costs):.3f}")

    print("\nPor campo (configuración propuesta)")
    per_field: Dict[str, Thresholds] = {}
    for field_id in sorted(set(field_ids)):
        rows = np.array([fid == field_id for fid in field_ids])
        score = score_from_masks(
            *decision_masks(chosen_features[rows], chosen),
            present[rows], absent[rows],
        )
        print(f"  {field_id:<22} {score.line()}")
        if args.por_campo and int(rows.sum()) >= args.min_muestras:
            base = Thresholds(
                pad_x=chosen.pad_x, pad_y=chosen.pad_y,
                ink_delta=chosen.ink_delta,
                max_ink_ratio=current.max_ink_ratio,
            )
            field_best = search(
                chosen_features[rows], present[rows], absent[rows],
                base, weights, args.min_resueltos,
            )
            per_field[field_id] = field_best[0]
            print(f"  {'':<22} propio: {field_best[1].line()}  "
                  f"({field_best[0].describe()})")

    print("\nErrores restantes")
    _report_errors(dataset, samples, labels, chosen_features, chosen,
                   args.max_errores)

    if applicable is not best:
        print(
            f"\nLa configuración propuesta pide otro margen de recorte: "
            f"SIGNATURE_PAD_X {current.pad_x:.2f} -> {proposed.pad_x:.2f}, "
            f"SIGNATURE_PAD_Y {current.pad_y:.2f} -> {proposed.pad_y:.2f}.\n"
            f"El margen son dos constantes de app/vision/signature.py, no está "
            f"en la plantilla, y esta herramienta no las toca: escribir solo "
            f"los umbrales dejaría una mezcla que nadie ha medido.\n"
            f"Por eso --aplicar escribe la fila 'margen actual' "
            f"(coste {chosen_cost:.3f} frente a {proposed_cost:.3f}). Si la "
            f"diferencia compensa, edite las constantes a mano y vuelva a "
            f"ejecutar: entonces las dos filas coincidirán."
        )

    if args.aplicar:
        if chosen_cost >= current_cost:
            print("\nNo se aplica nada: la propuesta no mejora a la actual.")
            return 0
        _apply_to_template(args.template, chosen,
                           per_field if args.por_campo else None)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Calibra los umbrales de firma con las etiquetas humanas",
    )
    parser.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("--campos", nargs="+", help="limitar a estos campos")
    parser.add_argument(
        "--solo-alineadas", action="store_true", dest="solo_alineadas",
        help="descartar las páginas cuya alineación no fue fiable",
    )
    parser.add_argument(
        "--rapido", action="store_true",
        help="rejilla reducida de márgenes y umbral de tinta",
    )
    parser.add_argument(
        "--por-campo", action="store_true", dest="por_campo",
        help="proponer además umbrales propios para cada campo",
    )
    parser.add_argument(
        "--min-muestras", type=int, default=40, dest="min_muestras",
        help="etiquetas mínimas para calibrar un campo por separado",
    )
    parser.add_argument("--cv", type=int, default=5,
                        help="particiones de validación cruzada (0 = ninguna)")
    parser.add_argument(
        "--min-resueltos", type=float, default=0.80, dest="min_resueltos",
        help="fracción mínima de recortes que la configuración debe resolver "
             "sin dejar en incierto (evita el detector que no se moja)",
    )
    parser.add_argument("--max-errores", type=int, default=25,
                        dest="max_errores")
    parser.add_argument("--coste-falso-presente", type=float,
                        default=COST_FALSE_PRESENT,
                        dest="coste_falso_presente")
    parser.add_argument("--coste-falso-ausente", type=float,
                        default=COST_FALSE_ABSENT,
                        dest="coste_falso_ausente")
    parser.add_argument("--coste-incierto", type=float, default=COST_UNCLEAR,
                        dest="coste_incierto")
    parser.add_argument(
        "--aplicar", action="store_true",
        help="escribir los umbrales propuestos en la plantilla",
    )
    args = parser.parse_args()
    try:
        return run(args)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

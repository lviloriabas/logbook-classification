"""Audita las causas de REVISAR sin modificar entregas ni conectarse a AirVault.

Ejemplo: portable\\python312\\tools\\python.exe tools\\estudiar_revision.py
    --entrada output --salida output/estudio_revision
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.models.schemas import ValidationReport
from app.reports.outputs import marcar_revision
from app.templates.manager import TemplateManager
from app.validation.date_review import review_date_window
from app.validation.discrepancias import clasificar_lote
from app.validation.page_status import has_log_number, has_matricula, needs_review


def estudiar(path: Path, template, reference: date | None = None) -> dict | None:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not data.get("reportes"):
        return None
    reports = [ValidationReport.model_validate(raw) for raw in data["reportes"]]
    # Se mide a la fecha de la ejecucion: envejecer hoy una entrega historica
    # crearia falsos motivos temporales en el propio estudio.
    reference = reference or date.fromisoformat(data["generado"][:10])
    anteriores = sum(p.discrepancy for r in reports for p in r.pages)
    clasificar_lote(reports, template)
    for report in reports:
        for page in report.pages:
            review_date_window(page, reference)
    faltantes = marcar_revision(reports, template)
    causes = Counter()
    detail = []
    complete = 0
    for report in reports:
        for page in report.pages:
            if not needs_review(page):
                continue
            missing = faltantes.get((report.source_filename, page.page_number), ())
            reasons = []
            if page.discrepancy:
                reasons.append("firmas")
            if page.date_review:
                reasons.append("fecha_por_confirmar")
            if page.blank:
                reasons.append("en_blanco")
            if not has_log_number(page):
                reasons.append("logpage_ilegible")
            if not has_matricula(page):
                reasons.append("avion_ilegible")
            reasons.extend(f"falta_{field}" for field in missing)
            causes.update(reasons)
            complete += not missing
            detail.append({
                "archivo": report.source_filename, "pagina": page.page_number,
                "log_number": next((f.value for f in page.fields if f.field_id == "log_number"), None),
                "causas": reasons, "faltantes": list(missing),
                "discrepancia": page.discrepancy_note,
                "comentario": page.comment,
            })
    total = sum(len(report.pages) for report in reports)
    return {
        "ejecucion": data.get("corrida") or path.stem,
        "referencia": reference.isoformat(), "origen": str(path.resolve()),
        "paginas": total, "revision": len(detail),
        "porcentaje_revision": round(100 * len(detail) / total, 2) if total else 0,
        "indice_completo_en_revision": complete,
        "discrepancias_guardadas": anteriores,
        "discrepancias_reglas_actuales": sum(p.discrepancy for r in reports for p in r.pages),
        "causas": dict(causes), "detalle": detail,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entrada", type=Path, default=ROOT / "output")
    parser.add_argument("--salida", type=Path, required=True)
    parser.add_argument("--plantilla", type=Path, default=ROOT / "template/aircraft_log.json")
    parser.add_argument("--fecha", type=date.fromisoformat)
    args = parser.parse_args()
    template = TemplateManager().load(args.plantilla)
    paths = [args.entrada] if args.entrada.is_file() else sorted(args.entrada.glob("BITS */datos/BITS *.json"))
    rows = []
    for path in paths:
        result = estudiar(path, template, args.fecha)
        if result is not None:
            rows.append(result)
    args.salida.mkdir(parents=True, exist_ok=True)
    (args.salida / "causas.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Estudio de revision de bitacoras", "",
        "Reproduccion de las reglas actuales sobre los resultados guardados. "
        "No vuelve a leer las imagenes ni valida la exactitud del OCR. "
        "Indice completo significa que los obligatorios pueden completarse, "
        "incluida la inferencia de fecha que ya usa el indexador; no demuestra "
        "que una firma o una fecha dudosa deban aprobarse.", "",
        "Cada fila es una ejecucion. Pueden contener los mismos escaneos: "
        "no se suman como documentos independientes. La referencia temporal "
        "es la fecha de generacion, salvo que se indique --fecha.", "",
        "| Ejecucion | Paginas | A revision | % | Indice completo en revision | Discrepancias guardadas | Discrepancias actuales |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append("| {ejecucion} | {paginas} | {revision} | {porcentaje_revision} | "
                     "{indice_completo_en_revision} | {discrepancias_guardadas} | "
                     "{discrepancias_reglas_actuales} |".format(**row))
    lines += ["", "Los motivos pueden coincidir en una pagina. causas.json conserva "
              "el detalle por archivo, pagina y logpage para una auditoria reproducible.", ""]
    (args.salida / "estudio.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"Estudiadas {len(rows)} ejecuciones. Resultados en {args.salida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Pruebas del re-export: las salidas se escriben sobre la carpeta de la
corrida (mismo CSV, PDFs regenerados, sin artefactos de la separación
anterior)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.models.schemas import FieldResult, PageResult, ValidationReport
from app.reports.outputs import OutputOptions, write_outputs
from app.templates.manager import TemplateManager

INPUT = Path(__file__).resolve().parents[1] / "input"
TEMPLATE = (
    Path(__file__).resolve().parents[1]
    / "template" / "aircraft_log.json"
)


def _page(pn: int, log, mat) -> PageResult:
    page = PageResult(page_number=pn)
    if log is not None:
        page.add_field(FieldResult(page_number=pn, field_id="log_number",
                                   field_type="ocr", value=log,
                                   confidence=1.0, status="OK"))
    if mat is not None:
        page.add_field(FieldResult(page_number=pn, field_id="matricula",
                                   field_type="ocr", value=mat,
                                   confidence=1.0, status="OK"))
    return page


def _reporte(name: str, *pages: PageResult) -> ValidationReport:
    return ValidationReport(pdf_path=str(INPUT / name), template_name="fixture",
                            pages=list(pages))


class TestReexport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = TemplateManager().load(TEMPLATE)
        cls.reports = [
            _reporte("test.pdf",
                     _page(1, "2147337", "HP-1534CMP"),
                     _page(2, "2147338", "HP-1534CMP"),
                     _page(3, "2271665", "HP-1538CMP")),
        ]

    def _options(self, root: Path, separar_por=(), run_dir=None) -> OutputOptions:
        return OutputOptions(
            template=self.template,
            output_root=root,
            dpi=100,
            crop_padding=0.01,
            separar_por=tuple(separar_por),
            run_dir=run_dir,
        )

    def test_reexport_misma_carpeta(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            primer = write_outputs(self.reports, self._options(root))
            nombre = primer.name
            csv_primero = primer / "datos" / f"{nombre}.CSV"
            self.assertTrue(csv_primero.exists())
            # Re-export con otra separación apuntando a la misma carpeta:
            # no se crea una corrida nueva y los PDFs se regeneran ahí.
            segundo = write_outputs(
                self.reports, self._options(root, ("avion",), primer)
            )
            self.assertEqual(primer, segundo)
            self.assertEqual(len(list(root.glob("BITS *"))), 1)
            self.assertTrue((primer / "datos" / f"{nombre}.CSV").exists())
            self.assertTrue((primer / "stats.json").exists())
            self.assertTrue((primer / "HP-1534CMP.pdf").exists())
            self.assertTrue((primer / "HP-1538CMP.pdf").exists())

    def test_limpieza_de_artefactos_previos(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = root / "BITS 07 AUG 2026 22 01"
            (run / "datos").mkdir(parents=True)
            (run / "logs").mkdir()
            (run / "HP-1234CMP.pdf").write_bytes(b"x")
            (run / "discrepancias.pdf").write_bytes(b"x")
            (run / "datos" / "BITS 07 AUG 2026 22 01.CSV").write_text(
                "old", encoding="utf-8")
            (run / "logs" / "app.log").write_text("log", encoding="utf-8")
            write_outputs(self.reports, self._options(root, (), run))
            self.assertFalse((run / "HP-1234CMP.pdf").exists())
            self.assertFalse((run / "discrepancias.pdf").exists())
            self.assertTrue(
                (run / "datos" / "BITS 07 AUG 2026 22 01.CSV").exists()
            )
            self.assertTrue((run / "logs" / "app.log").exists())

    def test_csv_conserva_nombre_y_contenido(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            primer = write_outputs(self.reports, self._options(root))
            nombre = primer.name
            csv_primero = (primer / "datos" / f"{nombre}.CSV").read_bytes()
            segundo = write_outputs(
                self.reports, self._options(root, ("mes",), primer)
            )
            csv_segundo = (segundo / "datos" / f"{nombre}.CSV").read_bytes()
            self.assertEqual(segundo, primer)
            self.assertEqual(csv_primero, csv_segundo)


if __name__ == "__main__":
    unittest.main()

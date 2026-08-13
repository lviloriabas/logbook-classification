"""Pruebas del organizador de PDFs: agrupación por avión/mes, orden por
libro y logpage, exclusión de discrepantes y nombres de archivo."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.models.schemas import FieldResult, PageResult, ValidationReport
from app.reports.organize import (
    _etiqueta_separador,
    _preparar_paginas,
    agrupar_paginas,
    clave_mes,
    escribir_pdf_unico,
    generar_pdfs,
    nombre_mes,
    ruta_pdf,
)
from app.utils.io import sanitize_filename

INPUT = Path(__file__).resolve().parents[1] / "input"


def _page(pn: int, log, mat, date=None) -> PageResult:
    page = PageResult(page_number=pn, date=date)
    if log is not None:
        page.add_field(FieldResult(page_number=pn, field_id="log_number",
                                   field_type="ocr", value=log,
                                   confidence=1.0, status="OK"))
    if mat is not None:
        page.add_field(FieldResult(page_number=pn, field_id="matricula",
                                   field_type="ocr", value=mat,
                                   confidence=1.0, status="OK"))
    return page


def _reporte(*pages: PageResult) -> ValidationReport:
    return ValidationReport(pdf_path="fixture.pdf", template_name="fixture",
                            pages=list(pages))


class TestAgrupar(unittest.TestCase):
    def test_por_avion(self):
        pages = [
            _page(1, "2147337", "HP-1534CMP"),
            _page(2, "2147338", "HP-1534CMP"),
            _page(3, "2271665", "HP-1538CMP"),
        ]
        grupos = agrupar_paginas([_reporte(*pages)], ["avion"], None)
        self.assertEqual(sorted(grupos), [("HP-1534CMP",), ("HP-1538CMP",)])
        self.assertEqual([g.page.page_number for g in grupos[("HP-1534CMP",)]],
                         [1, 2])

    def test_orden_por_libro_y_logpage(self):
        pages = [
            _page(1, "2147350", "HP-1534CMP"),
            _page(2, "2147301", "HP-1534CMP"),
            _page(3, "2147300", "HP-1534CMP"),
        ]
        grupos = agrupar_paginas([_reporte(*pages)], ["avion"], None)
        numeros = [g.page.page_number for g in grupos[("HP-1534CMP",)]]
        self.assertEqual(numeros, [3, 2, 1])

    def test_sin_logpage_va_al_final_en_orden_original(self):
        pages = [
            _page(1, None, "HP-1534CMP"),
            _page(2, "2147300", "HP-1534CMP"),
            _page(3, "2147301", "HP-1534CMP"),
            _page(4, None, "HP-1534CMP"),
        ]
        grupos = agrupar_paginas([_reporte(*pages)], ["avion"], None)
        numeros = [g.page.page_number for g in grupos[("HP-1534CMP",)]]
        self.assertEqual(numeros, [2, 3, 1, 4])

    def test_por_mes(self):
        pages = [
            _page(1, "2147300", "HP-1534CMP", date="2026/07/15"),
            _page(2, "2147301", "HP-1534CMP", date="2026/08/02"),
            _page(3, "2147302", "HP-1534CMP", date="2026/07/28"),
        ]
        grupos = agrupar_paginas([_reporte(*pages)], ["mes"], None)
        self.assertEqual(sorted(grupos), [("2026-07",), ("2026-08",)])
        self.assertEqual(len(grupos[("2026-07",)]), 2)

    def test_avion_y_mes_combinados(self):
        pages = [
            _page(1, "2147300", "HP-1534CMP", date="2026/07/15"),
            _page(2, "2147301", "HP-1534CMP", date="2026/08/02"),
            _page(3, "2271650", "HP-1538CMP", date="2026/07/28"),
        ]
        grupos = agrupar_paginas([_reporte(*pages)], ["avion", "mes"], None)
        self.assertEqual(sorted(grupos), [
            ("HP-1534CMP", "2026-07"),
            ("HP-1534CMP", "2026-08"),
            ("HP-1538CMP", "2026-07"),
        ])

    def test_fallbacks_sin_matricula_y_sin_fecha(self):
        pages = [
            _page(1, "2147300", None, date="2026/07/15"),
            _page(2, "2147301", "HP-1534CMP", date=None),
        ]
        grupos = agrupar_paginas([_reporte(*pages)], ["avion", "mes"], None)
        self.assertIn(("sin_matricula", "2026-07"), grupos)
        self.assertIn(("HP-1534CMP", "sin_fecha"), grupos)

    def test_pagina_en_blanco_excluida(self):
        pagina = _page(1, "2147300", "HP-1534CMP")
        pagina.blank = True
        grupos = agrupar_paginas([_reporte(pagina)], ["avion"], None)
        self.assertEqual(grupos, {})

    def test_excluidas_no_se_incluyen(self):
        pages = [
            _page(1, "2147300", "HP-1534CMP"),
            _page(2, "2147301", "HP-1534CMP"),
        ]
        excluidas = {("fixture.pdf", 1)}
        grupos = agrupar_paginas([_reporte(*pages)], ["avion"], excluidas)
        self.assertEqual([g.page.page_number for g in grupos[("HP-1534CMP",)]],
                         [2])

    def test_sin_condiciones_una_sola_clave(self):
        pages = [_page(1, "2147300", "HP-1534CMP")]
        grupos = agrupar_paginas([_reporte(*pages)], [], None)
        self.assertEqual(list(grupos), [()])

    def test_condicion_desconocida(self):
        with self.assertRaises(ValueError):
            agrupar_paginas([_reporte()], ["dia"], None)

    def test_clave_mes_invalida(self):
        pagina = _page(1, "2147300", "HP-1534CMP", date="basura")
        self.assertEqual(clave_mes(pagina), "sin_fecha")


class TestPrepararPaginas(unittest.TestCase):
    def test_orden_original_con_exclusiones(self):
        blanca = _page(1, "2147300", "HP-1534CMP")
        blanca.blank = True
        refs = _preparar_paginas(
            [_reporte(blanca, _page(2, "2147301", "HP-1534CMP"),
                      _page(3, "2147302", "HP-1538CMP"))],
            {("fixture.pdf", 3)},
        )
        self.assertEqual([r.page.page_number for r in refs], [2])

    def test_etiquetas_separador(self):
        self.assertEqual(_etiqueta_separador("mes", "2026-07"), "2026/JUL")
        self.assertEqual(_etiqueta_separador("mes", "sin_fecha"),
                         "sin_fecha")
        self.assertEqual(_etiqueta_separador("avion", "HP-1534CMP"),
                         "HP-1534CMP")


class TestNombres(unittest.TestCase):
    def test_sin_condiciones(self):
        self.assertEqual(ruta_pdf((), []), Path("bitacoras.pdf"))
        run_dir = Path("BITS 06 AUG 2026 22 55")
        self.assertEqual(
            ruta_pdf((), [], run_dir),
            Path("BITS 06 AUG 2026 22 55.pdf"),
        )

    def test_por_avion(self):
        self.assertEqual(ruta_pdf(("HP-1534CMP",), ["avion"]),
                         Path("HP-1534CMP.pdf"))
        self.assertEqual(ruta_pdf(("sin_matricula",), ["avion"]),
                         Path("sin_matricula.pdf"))

    def test_por_mes(self):
        self.assertEqual(ruta_pdf(("2026-07",), ["mes"]),
                         Path("2026-JUL.pdf"))
        self.assertEqual(ruta_pdf(("sin_fecha",), ["mes"]),
                         Path("sf.pdf"))

    def test_combinado_carpeta_por_matricula(self):
        self.assertEqual(
            ruta_pdf(("HP-1534CMP", "2026-07"), ["avion", "mes"]),
            Path("HP-1534CMP") / "2026-JUL.pdf")
        self.assertEqual(
            ruta_pdf(("HP-1534CMP", "sin_fecha"), ["avion", "mes"]),
            Path("HP-1534CMP") / "sf.pdf")
        self.assertEqual(
            ruta_pdf(("sin_matricula", "sin_fecha"), ["avion", "mes"]),
            Path("sin_matricula") / "sf.pdf")

    def test_nombre_mes(self):
        self.assertEqual(nombre_mes("2026-07"), "2026-JUL")
        self.assertEqual(nombre_mes("sin_fecha"), "sf")

    def test_caracteres_peligrosos(self):
        self.assertEqual(
            sanitize_filename("HP-15<4>CMP"),
            "HP-15_4_CMP")


@unittest.skipUnless(INPUT.joinpath("test.pdf").exists(),
                     "requiere input/test.pdf")
class TestPdfsOrdenados(unittest.TestCase):
    def test_genera_pdf_por_avion(self):
        reporte = ValidationReport(
            pdf_path=str(INPUT / "test.pdf"), template_name="fixture",
            pages=[_page(1, "2147300", "HP-1534CMP")],
        )
        run_dir = Path(tempfile.mkdtemp())
        rutas = generar_pdfs([reporte], run_dir, ["avion"], None, dpi=100)
        self.assertEqual(len(rutas), 1)
        self.assertEqual(rutas[0].name, "HP-1534CMP.pdf")
        self.assertGreater(rutas[0].stat().st_size, 0)
        import pymupdf as fitz

        with fitz.open(str(INPUT / "test.pdf")) as source, \
                fitz.open(str(rutas[0])) as output:
            self.assertEqual(output[0].rect, source[0].rect)
            self.assertEqual(output[0].get_text(), source[0].get_text())

    def test_exportacion_no_renderiza_las_paginas(self):
        reporte = ValidationReport(
            pdf_path=str(INPUT / "test.pdf"), template_name="fixture",
            pages=[_page(1, "2147300", "HP-1534CMP")],
        )
        run_dir = Path(tempfile.mkdtemp())
        with patch("app.reports.organize.render_page",
                   side_effect=AssertionError("no debe rasterizar")):
            rutas = generar_pdfs([reporte], run_dir, ["avion"], None,
                                 dpi=100)
        self.assertEqual(len(rutas), 1)

    def test_combinado_crea_carpeta_por_matricula(self):
        reporte = ValidationReport(
            pdf_path=str(INPUT / "test.pdf"), template_name="fixture",
            pages=[_page(1, "2147300", "HP-1534CMP", date="2026/07/15")],
        )
        run_dir = Path(tempfile.mkdtemp())
        rutas = generar_pdfs([reporte], run_dir, ["avion", "mes"], None,
                             dpi=100)
        self.assertEqual(len(rutas), 1)
        self.assertEqual(rutas[0],
                         run_dir / "HP-1534CMP" / "2026-JUL.pdf")
        self.assertGreater(rutas[0].stat().st_size, 0)

    def test_combinado_sin_fecha_es_sf(self):
        reporte = ValidationReport(
            pdf_path=str(INPUT / "test.pdf"), template_name="fixture",
            pages=[_page(1, "2147300", "HP-1534CMP", date=None)],
        )
        run_dir = Path(tempfile.mkdtemp())
        rutas = generar_pdfs([reporte], run_dir, ["avion", "mes"], None,
                             dpi=100)
        self.assertEqual(rutas[0], run_dir / "HP-1534CMP" / "sf.pdf")

    def test_pdf_unico_con_separador_matricula(self):
        reporte = ValidationReport(
            pdf_path=str(INPUT / "test.pdf"), template_name="fixture",
            pages=[
                _page(1, "2147300", "HP-1534CMP"),
                _page(2, "2271665", "HP-1538CMP"),
            ],
        )
        run_dir = Path(tempfile.mkdtemp())
        ruta = escribir_pdf_unico(
            [reporte], run_dir, ["avion"], None, dpi=100
        )
        self.assertEqual(ruta, run_dir / f"{run_dir.name}.pdf")
        import pymupdf as fitz

        doc = fitz.open(str(ruta))
        self.assertEqual(doc.page_count, 3)  # 2 escaneos + 1 divisor
        self.assertEqual(doc.load_page(1).get_text().strip(),
                         "HP-1538CMP")
        doc.close()

    def test_pdf_unico_plano_sin_divisores(self):
        reporte = ValidationReport(
            pdf_path=str(INPUT / "test.pdf"), template_name="fixture",
            pages=[_page(1, "2147300", "HP-1534CMP")],
        )
        run_dir = Path(tempfile.mkdtemp())
        ruta = escribir_pdf_unico([reporte], run_dir, [], None, dpi=100)
        import pymupdf as fitz

        doc = fitz.open(str(ruta))
        self.assertEqual(doc.page_count, 1)
        self.assertEqual(doc.load_page(0).get_text().strip(), "")
        doc.close()

    def test_pdf_unico_separador_mes_dentro_de_matricula(self):
        reporte = ValidationReport(
            pdf_path=str(INPUT / "test.pdf"), template_name="fixture",
            pages=[
                _page(1, "2147300", "HP-1534CMP", date="2026/07/15"),
                _page(2, "2147301", "HP-1534CMP", date="2026/08/02"),
            ],
        )
        run_dir = Path(tempfile.mkdtemp())
        ruta = escribir_pdf_unico(
            [reporte], run_dir, ["mes"], None, dpi=100
        )
        import pymupdf as fitz

        doc = fitz.open(str(ruta))
        self.assertEqual(doc.page_count, 3)
        self.assertEqual(doc.load_page(1).get_text().strip(), "2026/AUG")
        doc.close()

    def test_pdf_unico_ambos_criterios_dos_tipos_de_divisor(self):
        reporte = ValidationReport(
            pdf_path=str(INPUT / "test.pdf"), template_name="fixture",
            pages=[
                _page(1, "2147300", "HP-1534CMP", date="2026/07/15"),
                _page(2, "2147301", "HP-1534CMP", date="2026/08/02"),
                _page(3, "2271665", "HP-1538CMP", date="2026/07/28"),
            ],
        )
        run_dir = Path(tempfile.mkdtemp())
        ruta = escribir_pdf_unico(
            [reporte], run_dir, ["avion", "mes"], None, dpi=100
        )
        import pymupdf as fitz

        doc = fitz.open(str(ruta))
        self.assertEqual(doc.page_count, 5)  # 3 escaneos + 2 divisores
        self.assertEqual(doc.load_page(1).get_text().strip(), "2026/AUG")
        self.assertEqual(doc.load_page(3).get_text().strip(), "HP-1538CMP")
        doc.close()


if __name__ == "__main__":
    unittest.main()

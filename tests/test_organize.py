"""Pruebas del organizador de PDFs: agrupación por avión/mes, orden por
libro y logpage, exclusión de discrepantes y nombres de archivo."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.models.schemas import FieldResult, PageResult, ValidationReport
from app.reports.organize import (
    _claves_en_orden_seccion,
    _etiqueta_grupo,
    _etiqueta_separador,
    _preparar_paginas,
    agrupar_paginas,
    paginas_para_revisar,
    clave_mes,
    escribir_pdf_discrepancias,
    escribir_pdf_unico,
    generar_pdfs,
    nombre_mes,
    ruta_pdf,
)
from app.utils.io import sanitize_filename
from app.validation.discrepancias import Discrepancia

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

    def test_mes_inferido_agrupa_aunque_el_dia_siga_vacio(self):
        page = _page(1, "2147300", "HP-1534CMP", date=None)
        page.add_field(FieldResult(
            page_number=1, field_id="month", field_type="ocr",
            value="JUL", confidence=0.6, status="WARNING",
        ))
        page.add_field(FieldResult(
            page_number=1, field_id="year", field_type="ocr",
            value="26", confidence=0.6, status="WARNING",
        ))

        grupos = agrupar_paginas([_reporte(page)], ["mes"], None)

        self.assertIn(("2026-07",), grupos)
        self.assertEqual(clave_mes(page), "2026-07")

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

    def test_secciones_ordenan_matricula_y_fecha_con_faltantes_al_final(self):
        pages = [
            _page(1, "1000001", "HP-1538CMP", date=None),
            _page(2, "9000001", "HP-1534CMP", date="2026/08/02"),
            _page(3, "9000002", "HP-1534CMP", date="2026/07/28"),
        ]
        criterios = ["avion", "mes"]
        grupos = agrupar_paginas([_reporte(*pages)], criterios, None)

        self.assertEqual(
            _claves_en_orden_seccion(grupos, criterios),
            [
                ("HP-1534CMP", "2026-07"),
                ("HP-1534CMP", "2026-08"),
                ("HP-1538CMP", "sin_fecha"),
            ],
        )

    def test_sin_avion_confirmado_va_a_revisar_y_no_a_un_grupo(self):
        """Una matrícula sin confirmar no abre grupo: abriría un avión falso."""
        pages = [
            _page(1, "2147300", None, date="2026/07/15"),
            _page(2, "2147301", "HP-1534CMP", date=None),
        ]
        reportes = [_reporte(*pages)]
        grupos = agrupar_paginas(reportes, ["avion", "mes"], None)
        self.assertEqual(list(grupos), [("HP-1534CMP", "sin_fecha")])
        self.assertEqual(
            [ref.page.page_number for ref in paginas_para_revisar(reportes)],
            [1],
        )

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

    def test_sin_criterios_tambien_ordena_por_logpage(self):
        refs = _preparar_paginas(
            [_reporte(
                _page(1, "2147302", "HP-1534CMP"),
                _page(2, "2147300", "HP-1534CMP"),
                _page(3, "2147301", "HP-1534CMP"),
            )],
            None,
        )
        self.assertEqual([r.page.page_number for r in refs], [2, 3, 1])

    def test_etiquetas_separador(self):
        self.assertEqual(_etiqueta_separador("mes", "2026-07"), "JUL 2026")
        self.assertEqual(_etiqueta_separador("mes", "sin_fecha"),
                         "SIN FECHA")
        self.assertEqual(_etiqueta_separador("avion", "HP-1534CMP"),
                         "HP-1534CMP")
        self.assertEqual(
            _etiqueta_grupo(
                ("HP-1534CMP", "2026-07"), ("avion", "mes")
            ),
            "HP-1534CMP\nJUL 2026",
        )


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

    def test_combinado_nombre_incluye_matricula_y_mes(self):
        self.assertEqual(
            ruta_pdf(("HP-1534CMP", "2026-07"), ["avion", "mes"]),
            Path("HP-1534CMP_2026-JUL.pdf"))
        self.assertEqual(
            ruta_pdf(("HP-1534CMP", "sin_fecha"), ["avion", "mes"]),
            Path("HP-1534CMP_sf.pdf"))
        self.assertEqual(
            ruta_pdf(("sin_matricula", "sin_fecha"), ["avion", "mes"]),
            Path("sin_matricula_sf.pdf"))
        self.assertEqual(
            ruta_pdf(("2026-07", "HP-1534CMP"), ["mes", "avion"]),
            Path("HP-1534CMP_2026-JUL.pdf"),
        )

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

    def test_combinado_archivo_incluye_matricula_y_mes(self):
        reporte = ValidationReport(
            pdf_path=str(INPUT / "test.pdf"), template_name="fixture",
            pages=[_page(1, "2147300", "HP-1534CMP", date="2026/07/15")],
        )
        run_dir = Path(tempfile.mkdtemp())
        rutas = generar_pdfs([reporte], run_dir, ["avion", "mes"], None,
                             dpi=100)
        self.assertEqual(len(rutas), 1)
        self.assertEqual(rutas[0],
                         run_dir / "HP-1534CMP_2026-JUL.pdf")
        self.assertGreater(rutas[0].stat().st_size, 0)

    def test_combinado_sin_fecha_es_sf(self):
        reporte = ValidationReport(
            pdf_path=str(INPUT / "test.pdf"), template_name="fixture",
            pages=[_page(1, "2147300", "HP-1534CMP", date=None)],
        )
        run_dir = Path(tempfile.mkdtemp())
        rutas = generar_pdfs([reporte], run_dir, ["avion", "mes"], None,
                             dpi=100)
        self.assertEqual(rutas[0], run_dir / "HP-1534CMP_sf.pdf")

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
        self.assertEqual(doc.page_count, 4)  # 2 escaneos + 2 divisores
        self.assertEqual(doc.load_page(0).get_text().strip(),
                         "HP-1534CMP")
        self.assertEqual(doc.load_page(2).get_text().strip(),
                         "HP-1538CMP")
        doc.close()

    def test_pdf_unico_cierra_con_el_separador_revisar(self):
        """La sección «Revisar» sale sin haber pedido nada más."""
        reporte = ValidationReport(
            pdf_path=str(INPUT / "test.pdf"), template_name="fixture",
            pages=[
                _page(1, "2147300", "HP-1534CMP"),
                _page(2, "2147301", None),
            ],
        )
        run_dir = Path(tempfile.mkdtemp())
        ruta = escribir_pdf_unico(
            [reporte], run_dir, ["avion"], None, dpi=100
        )
        import pymupdf as fitz

        doc = fitz.open(str(ruta))
        # 2 escaneos + separador del avión + separador de revisión
        self.assertEqual(doc.page_count, 4)
        self.assertEqual(doc.load_page(0).get_text().strip(), "HP-1534CMP")
        self.assertEqual(doc.load_page(2).get_text().strip(), "REVISAR")
        doc.close()

    def test_pdf_unico_sin_criterios_tambien_separa_revisar(self):
        reporte = ValidationReport(
            pdf_path=str(INPUT / "test.pdf"), template_name="fixture",
            pages=[
                _page(1, "2147300", "HP-1534CMP"),
                _page(2, "2147301", None),
            ],
        )
        run_dir = Path(tempfile.mkdtemp())
        ruta = escribir_pdf_unico([reporte], run_dir, [], None, dpi=100)
        import pymupdf as fitz

        doc = fitz.open(str(ruta))
        self.assertEqual(doc.page_count, 3)
        self.assertEqual(doc.load_page(1).get_text().strip(), "REVISAR")
        doc.close()

    def test_varios_pdf_escriben_revisar_pdf(self):
        reporte = ValidationReport(
            pdf_path=str(INPUT / "test.pdf"), template_name="fixture",
            pages=[
                _page(1, "2147300", "HP-1534CMP"),
                _page(2, "2147301", None),
            ],
        )
        run_dir = Path(tempfile.mkdtemp())
        rutas = generar_pdfs([reporte], run_dir, ["avion"], None, dpi=100)
        self.assertEqual([ruta.name for ruta in rutas],
                         ["HP-1534CMP.pdf", "revisar.pdf"])

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

    def test_pdf_unico_sin_paginas_conserva_el_anterior(self):
        """Sin nada que exportar no se toca el PDF de la entrega previa."""
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "salida"
            run_dir.mkdir()
            previo = run_dir / f"{run_dir.name}.pdf"
            previo.write_bytes(b"anterior")
            ruta = escribir_pdf_unico([], run_dir, [], None, dpi=100)
            self.assertEqual(ruta, previo)
            self.assertEqual(previo.read_bytes(), b"anterior")

    def test_pdf_unico_no_pisa_el_anterior(self):
        """Re-exportar deja el PDF previo intacto y numera el nuevo."""
        reporte = ValidationReport(
            pdf_path=str(INPUT / "test.pdf"), template_name="fixture",
            pages=[_page(1, "2147300", "HP-1534CMP")],
        )
        run_dir = Path(tempfile.mkdtemp())
        primera = escribir_pdf_unico([reporte], run_dir, [], None, dpi=100)
        tamano = primera.stat().st_size
        segunda = escribir_pdf_unico([reporte], run_dir, [], None, dpi=100)
        self.assertEqual(segunda, run_dir / f"{run_dir.name}-2.pdf")
        self.assertEqual(primera.stat().st_size, tamano)

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
        self.assertEqual(doc.page_count, 4)
        self.assertEqual(doc.load_page(0).get_text().strip(), "JUL 2026")
        self.assertEqual(doc.load_page(2).get_text().strip(), "AUG 2026")
        doc.close()

    def test_pdf_unico_ambos_criterios_en_cada_divisor(self):
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
        self.assertEqual(doc.page_count, 6)  # 3 escaneos + 3 divisores
        self.assertEqual(
            doc.load_page(0).get_text().strip(), "HP-1534CMP\nJUL 2026"
        )
        self.assertEqual(
            doc.load_page(2).get_text().strip(), "HP-1534CMP\nAUG 2026"
        )
        self.assertEqual(
            doc.load_page(4).get_text().strip(), "HP-1538CMP\nJUL 2026"
        )
        doc.close()

    def test_pdf_unico_discrepancias_al_final_sin_subdivisiones(self):
        import pymupdf as fitz

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source_path = tmp_path / "source.pdf"
            source = fitz.open()
            for label in ("PAGINA 1", "PAGINA 2", "PAGINA 3"):
                source.new_page().insert_text((72, 72), label)
            source.save(str(source_path))
            source.close()

            reporte = ValidationReport(
                pdf_path=str(source_path),
                template_name="fixture",
                pages=[
                    _page(1, "2147302", "HP-1534CMP", "2026/08/01"),
                    _page(2, "2147301", "HP-1538CMP", "2026/07/01"),
                    _page(3, "2147300", "HP-1534CMP", "2026/06/01"),
                ],
            )
            run_dir = tmp_path / "salida"
            ruta = escribir_pdf_unico(
                [reporte],
                run_dir,
                ["avion", "mes"],
                {("source.pdf", 2), ("source.pdf", 3)},
                dpi=100,
                discrepancias_al_final=True,
            )

            with fitz.open(str(ruta)) as doc:
                textos = [page.get_text().strip() for page in doc]
                self.assertGreater(doc.load_page(0).rect.width,
                                   doc.load_page(0).rect.height)
            self.assertEqual(
                textos,
                [
                    "HP-1534CMP\nAUG 2026",
                    "PAGINA 1",
                    "POSIBLES DISCREPANCIAS",
                    "PAGINA 3",
                    "PAGINA 2",
                ],
            )

    def test_pdf_discrepancias_tiene_portada_y_orden_global(self):
        import pymupdf as fitz

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source_path = tmp_path / "source.pdf"
            source = fitz.open()
            for label in ("PAGINA 1", "PAGINA 2", "PAGINA 3"):
                source.new_page().insert_text((72, 72), label)
            source.save(str(source_path))
            source.close()
            entradas = [
                Discrepancia(
                    pdf_path=str(source_path),
                    page_number=2,
                    log_number=2147301,
                    tipo="vuelo",
                    categoria="missing",
                ),
                Discrepancia(
                    pdf_path=str(source_path),
                    page_number=3,
                    log_number=2147300,
                    tipo="vuelo",
                    categoria="uncertain",
                ),
            ]

            ruta = escribir_pdf_discrepancias(
                entradas, None, tmp_path / "salida", dpi=100
            )
            self.assertEqual(ruta.name, "discrepancias.pdf")

            with fitz.open(str(ruta)) as doc:
                textos = [page.get_text().strip() for page in doc]
            self.assertEqual(
                textos,
                ["POSIBLES DISCREPANCIAS", "PAGINA 3", "PAGINA 2"],
            )


if __name__ == "__main__":
    unittest.main()

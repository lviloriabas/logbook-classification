"""El PDF de entrega declara qué hay en cada una de sus páginas.

El CSV describe las bitácoras; el PDF además lleva separadores. Sin saber
en qué posiciones están, emparejar los dos por posición se desalinea en el
primer separador y el indexado escribiría cada dato una página más allá de
donde va. Estas pruebas fijan que la secuencia declarada sea exactamente la
que se escribe.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.models.schemas import FieldResult, PageResult, ValidationReport
from app.reports.organize import (
    ETIQUETA_REVISAR,
    escribir_indice_paginas,
    secuencia_pdf_unico,
)


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


def etiquetas(secuencia) -> list:
    """Secuencia en corto: la etiqueta del separador o el número de página."""
    return [
        e.separador if e.ref is None else e.ref.page.page_number
        for e in secuencia
    ]


# ── secuencia ──────────────────────────────────────────────────────

def test_sin_separar_no_hay_ninguna_divisoria():
    reporte = _reporte(
        _page(1, "2147337", "HP-1534CMP"),
        _page(2, "2147338", "HP-1534CMP"),
    )
    assert etiquetas(secuencia_pdf_unico([reporte])) == [1, 2]


def test_cada_grupo_abre_con_su_separador():
    reporte = _reporte(
        _page(1, "2147337", "HP-1534CMP"),
        _page(2, "2271665", "HP-1538CMP"),
    )
    secuencia = secuencia_pdf_unico([reporte], ["avion"])
    assert etiquetas(secuencia) == ["HP-1534CMP", 1, "HP-1538CMP", 2]


def test_las_paginas_sin_matricula_cierran_bajo_revisar():
    reporte = _reporte(
        _page(1, "2147337", "HP-1534CMP"),
        _page(2, "2147338", None),
    )
    assert etiquetas(secuencia_pdf_unico([reporte])) == [1, ETIQUETA_REVISAR, 2]


def test_las_discrepancias_van_al_final_con_su_separador():
    reporte = _reporte(
        _page(1, "2147337", "HP-1534CMP"),
        _page(2, "2147338", "HP-1534CMP"),
    )
    secuencia = secuencia_pdf_unico(
        [reporte], excluidas={("fixture.pdf", 2)},
        discrepancias_al_final=True,
    )
    assert etiquetas(secuencia) == [1, "POSIBLES DISCREPANCIAS", 2]


def test_la_pagina_en_blanco_no_entra():
    """No llega al PDF, asi que tampoco puede ocupar una pagina del lote."""
    en_blanco = PageResult(page_number=2, blank=True)
    reporte = _reporte(_page(1, "2147337", "HP-1534CMP"), en_blanco)
    assert etiquetas(secuencia_pdf_unico([reporte])) == [1]


def test_sin_bitacoras_no_hay_secuencia():
    assert secuencia_pdf_unico([_reporte()]) == []


def test_la_secuencia_cuenta_lo_mismo_que_el_pdf():
    """Es la condicion que sostiene toda la correspondencia por posicion."""
    reporte = _reporte(
        _page(1, "2147337", "HP-1534CMP"),
        _page(2, "2271665", "HP-1538CMP"),
        _page(3, "2147338", None),
    )
    secuencia = secuencia_pdf_unico([reporte], ["avion"])
    # dos grupos con su separador, mas el separador de revisar y su pagina
    assert len(secuencia) == 6
    assert sum(1 for e in secuencia if e.es_separador) == 3


# ── archivo escrito ────────────────────────────────────────────────

def test_el_indice_describe_separadores_y_bitacoras(tmp_path):
    reporte = _reporte(
        _page(1, "2147337", "HP-1534CMP"),
        _page(2, "2147338", None),
    )
    destino = escribir_indice_paginas(
        secuencia_pdf_unico([reporte]), tmp_path / "corrida_paginas.json",
        "corrida.pdf",
    )
    datos = json.loads(Path(destino).read_text(encoding="utf-8"))

    assert datos["version"] == 1 and datos["pdf"] == "corrida.pdf"
    assert datos["paginas"] == [
        {"archivo": "fixture.pdf", "pagina": 1},
        {"separador": ETIQUETA_REVISAR},
        {"archivo": "fixture.pdf", "pagina": 2},
    ]


def test_el_indice_se_escribe_aunque_no_exista_la_carpeta(tmp_path):
    destino = escribir_indice_paginas([], tmp_path / "datos" / "x.json")
    assert Path(destino).is_file()
    assert json.loads(Path(destino).read_text(encoding="utf-8"))["paginas"] == []

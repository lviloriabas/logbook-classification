"""Descarte de páginas repetidas o en blanco de una corrida ya procesada.

Comprueba el criterio que comparten la ventana principal y el visor de CSV:
qué página se va, cuál se conserva de un log_number repetido y qué queda en
el resumen de cada reporte cuando la corrida se reescribe sin ellas.
"""

from __future__ import annotations

import pytest

from app.models.schemas import FieldResult, PageResult, ValidationReport
from app.validation.depuracion import contar_depuracion, depurar


def pagina(numero: int, log: str | None = None, blank: bool = False) -> PageResult:
    campos = []
    if log is not None:
        campos.append(
            FieldResult(
                page_number=numero,
                field_id="log_number",
                field_type="text",
                value=log,
            )
        )
    return PageResult(page_number=numero, blank=blank, fields=campos)


def reporte(nombre: str, paginas: list[PageResult]) -> ValidationReport:
    return ValidationReport(
        pdf_path=f"C:/input/{nombre}",
        template_name="bitacora",
        pages=paginas,
    )


@pytest.fixture
def corrida() -> list[ValidationReport]:
    """Dos PDF: un log_number repetido entre ellos y dos páginas en blanco."""
    return [
        reporte(
            "A.pdf",
            [
                pagina(1, "1000010"),
                pagina(2, "1000011"),
                pagina(3, blank=True),
            ],
        ),
        reporte(
            "B.pdf",
            [
                pagina(1, "1000011"),
                pagina(2, "1000012"),
                pagina(3, blank=True),
            ],
        ),
    ]


def test_cuenta_por_criterio_sin_tocar_los_reportes(corrida):
    resumen = contar_depuracion(corrida, duplicados=True, en_blanco=True)

    assert (resumen.duplicadas, resumen.en_blanco, resumen.total) == (1, 2, 3)
    assert [len(r.pages) for r in corrida] == [3, 3]


def test_solo_cuenta_el_criterio_marcado(corrida):
    solo_blancas = contar_depuracion(corrida, duplicados=False, en_blanco=True)

    assert solo_blancas.duplicadas == 0
    assert solo_blancas.total == 2


def test_una_pagina_repetida_y_en_blanco_se_cuenta_una_sola_vez():
    reports = [reporte("A.pdf", [pagina(1, "1000010"), pagina(2, "1000010", blank=True)])]

    resumen = contar_depuracion(reports, duplicados=True, en_blanco=True)

    assert (resumen.duplicadas, resumen.en_blanco) == (1, 1)
    assert resumen.total == 1


def test_conserva_la_primera_aparicion_del_log_number(corrida):
    quedan, resumen = depurar(corrida, duplicados=True, en_blanco=False)

    assert resumen.total == 1
    assert [p.page_number for p in quedan[0].pages] == [1, 2, 3]
    assert [p.page_number for p in quedan[1].pages] == [2, 3]


def test_quita_las_blancas_y_recalcula_el_resumen(corrida):
    quedan, _ = depurar(corrida, duplicados=False, en_blanco=True)

    assert all(not p.blank for r in quedan for p in r.pages)
    assert quedan[0].summary["total_pages"] == 2
    assert quedan[0].summary["blank_pages"] == 0


def test_el_pdf_que_queda_sin_paginas_sale_de_la_corrida():
    reports = [
        reporte("A.pdf", [pagina(1, "1000010")]),
        reporte("B.pdf", [pagina(1, blank=True), pagina(2, blank=True)]),
    ]

    quedan, resumen = depurar(reports, duplicados=False, en_blanco=True)

    assert resumen.total == 2
    assert [r.pdf_path for r in quedan] == ["C:/input/A.pdf"]


def test_sin_nada_marcado_no_se_quita_ninguna_pagina(corrida):
    quedan, resumen = depurar(corrida, duplicados=False, en_blanco=False)

    assert not resumen
    assert [len(r.pages) for r in quedan] == [3, 3]


def test_el_log_number_ilegible_no_es_duplicado():
    reports = [reporte("A.pdf", [pagina(1, "123"), pagina(2, "123"), pagina(3)])]

    resumen = contar_depuracion(reports, duplicados=True, en_blanco=True)

    assert resumen.total == 0

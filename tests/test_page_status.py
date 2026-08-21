"""Pruebas de la política de estado de página.

El estado dice qué se pudo indexar de la bitácora, no cuántos campos
salieron perfectos: una firma ausente es lo normal en media bitácora y una
casilla suelta de la fecha no decide nada.
"""

from __future__ import annotations

from app.models.schemas import FieldResult, PageResult, Status
from app.validation.page_status import (
    page_status,
    ready_for_auto_index,
    recompute_page_status,
)


def _field(field_id: str, value, status=Status.OK, field_type="ocr"):
    return FieldResult(
        page_number=1,
        field_id=field_id,
        field_type=field_type,
        value=value,
        confidence=0.9,
        status=status,
    )


def _page(**valores) -> PageResult:
    """Página con los campos de la plantilla real de bitácoras."""
    defaults = {
        "log_number": "2147300",
        "matricula": "HP-1534CMP",
        "day": "15",
        "month": "JUL",
        "year": "26",
    }
    defaults.update(valores)
    page = PageResult(page_number=1, date=None)
    for field_id, value in defaults.items():
        page.add_field(_field(field_id, value))
    return page


def test_una_pagina_completa_queda_en_ok():
    assert page_status(_page()) is Status.OK


def test_una_firma_ausente_no_convierte_la_pagina_en_error():
    """Una bitácora de vuelo no lleva firma de técnico: no es un error."""
    page = _page()
    page.add_field(_field(
        "technician_signature", "false", Status.ERROR, field_type="signature"
    ))
    assert page_status(page) is Status.OK


def test_una_casilla_suelta_de_la_fecha_no_degrada_la_pagina():
    page = _page()
    page.add_field(_field("month_3", None, Status.ERROR))
    assert page_status(page) is Status.OK


def test_el_numero_de_vuelo_no_decide_el_estado():
    """Es opcional y no forma parte del índice."""
    page = _page()
    page.add_field(_field("flight_number", None, Status.WARNING))
    assert page_status(page) is Status.OK


def test_un_dato_del_indice_que_falta_deja_la_pagina_por_revisar():
    assert page_status(_page(matricula=None)) is Status.WARNING
    assert page_status(_page(month=None)) is Status.WARNING


def test_un_dato_inferido_deja_la_pagina_por_revisar():
    page = _page()
    next(f for f in page.fields if f.field_id == "month").status = (
        Status.WARNING
    )
    assert page_status(page) is Status.WARNING


def test_solo_es_error_la_pagina_de_la_que_no_se_pudo_leer_nada():
    page = _page(log_number=None, matricula=None,
                 day=None, month=None, year=None)
    assert page_status(page) is Status.ERROR


def test_la_pagina_en_blanco_es_error():
    page = _page()
    page.blank = True
    assert page_status(page) is Status.ERROR


def test_recompute_escribe_el_estado_en_la_pagina():
    page = _page(matricula=None)
    page.status = Status.OK
    recompute_page_status(page)
    assert page.status is Status.WARNING


def test_autoindex_rechaza_una_matricula_marcada_por_confianza_baja():
    page = _page()
    matricula = next(
        field for field in page.fields if field.field_id == "matricula"
    )
    matricula.confidence = 0.49
    matricula.status = Status.WARNING
    assert page_status(page) is Status.WARNING
    assert not ready_for_auto_index(page)


def test_autoindex_admite_una_fecha_inferida_en_warning():
    page = _page()
    month = next(
        field for field in page.fields if field.field_id == "month"
    )
    month.status = Status.WARNING
    month.source = "inferred"
    month.inference_method = "book_consensus"
    assert page_status(page) is Status.WARNING
    assert ready_for_auto_index(page)


def test_autoindex_admite_consenso_fuerte_del_libro():
    page = _page()
    matricula = next(
        field for field in page.fields if field.field_id == "matricula"
    )
    matricula.source = "book_correction"
    matricula.votes = 3
    matricula.confidence = 0.70
    assert ready_for_auto_index(page)


def test_autoindex_rechaza_inferencia_con_un_solo_voto():
    page = _page()
    matricula = next(
        field for field in page.fields if field.field_id == "matricula"
    )
    matricula.source = "book_correction"
    matricula.votes = 1
    assert not ready_for_auto_index(page)


def test_autoindex_rechaza_inferencia_antigua_sin_conteo_de_votos():
    page = _page()
    matricula = next(
        field for field in page.fields if field.field_id == "matricula"
    )
    matricula.source = "book_correction"
    matricula.votes = None
    assert not ready_for_auto_index(page)

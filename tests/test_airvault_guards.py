"""Las guardas son lo unico que impide escribir en la bitacora equivocada."""

from __future__ import annotations

import pytest

from app.airvault.config import CAMPO_LOG_NUMBER, CAMPO_MATRICULA
from app.airvault.guards import (
    ErrorDeGuarda,
    verificar_alineacion,
    verificar_cantidad,
    verificar_duplicados,
    verificar_matriculas,
    verificar_no_pisar,
    verificar_obligatorios,
)
from app.airvault.mapping import valores_de_indice
from app.airvault.model import Registro


def registro(seq=1, matricula="HP-1848CMP", log="2287325",
             fecha="2026/08/31", fleet="NG"):
    return Registro(seq=seq, matricula=matricula, log_number=log,
                    fecha=fecha, fleet=fleet)


def test_cantidad_igual_pasa():
    verificar_cantidad([registro(1), registro(2)], 2)


def test_cantidad_distinta_corta():
    with pytest.raises(ErrorDeGuarda) as exc:
        verificar_cantidad([registro(1), registro(2)], 3)
    assert "3" in str(exc.value) and "2" in str(exc.value)


def test_matricula_fuera_del_picklist():
    avisos = verificar_matriculas([registro(matricula="HP-9999CMP")],
                                  ["HP-1848CMP"])
    assert [a.codigo for a in avisos] == ["matricula_desconocida"]


def test_matricula_vacia_se_acusa():
    avisos = verificar_matriculas([registro(matricula="")], ["HP-1848CMP"])
    assert [a.codigo for a in avisos] == ["matricula_vacia"]


def test_picklist_vacio_no_inventa_errores():
    # Si no se pudo leer el catalogo no se puede acusar a nadie.
    assert verificar_matriculas([registro()], []) == []


def test_obligatorio_vacio_se_acusa():
    reg = registro(fecha="")
    valores = valores_de_indice(reg, "Log Page", "PUBLISHED")
    avisos = verificar_obligatorios(reg, valores)
    assert [a.codigo for a in avisos] == ["obligatorio_vacio"]


def test_todos_los_obligatorios_llenos_no_avisa():
    reg = registro()
    valores = valores_de_indice(reg, "Log Page", "PUBLISHED")
    assert verificar_obligatorios(reg, valores) == []


def test_log_distinto_es_desalineacion():
    avisos = verificar_alineacion(registro(log="2287325"),
                                  {CAMPO_LOG_NUMBER: "2287999"})
    assert [a.codigo for a in avisos] == ["desalineado"]


def test_una_prueba_controlada_puede_permitir_log_distinto():
    assert verificar_alineacion(
        registro(log="7777777"),
        {CAMPO_LOG_NUMBER: "2287325"},
        permitir_log_distinto=True,
    ) == []


def test_log_igual_no_avisa():
    assert verificar_alineacion(registro(log="2287325"),
                                {CAMPO_LOG_NUMBER: "2287325"}) == []


def test_sin_dato_remoto_se_sigue_por_posicion():
    assert verificar_alineacion(registro(), {}) == []


def test_la_matricula_de_quick_upload_nunca_bloquea():
    """Es la del archivo entero, puesta por nosotros: no dice nada.

    Quick Upload clasifica el PDF completo con el Aircraft de su primera
    bitacora, asi que toda pagina cuyo avion sea otro llega con la matricula
    «distinta». Tomarla por evidencia dejaba el batch entero sin indexar y
    en amarillo: ni una sola pagina llegaba a escribirse.
    """
    for estado in (None, 1, 2, 3):
        avisos = verificar_alineacion(
            registro(matricula="HP-1848CMP"),
            {CAMPO_MATRICULA: "HP-1852CMP"},
            estado_pagina=estado,
        )

        assert avisos == [], f"estado {estado}"


def test_matricula_distinta_si_bloquea_una_pagina_verde():
    avisos = verificar_alineacion(
        registro(matricula="HP-1848CMP"),
        {CAMPO_MATRICULA: "HP-1852CMP"},
        estado_pagina=0,
    )

    assert [aviso.codigo for aviso in avisos] == ["matricula_distinta"]


def test_pagina_ya_valida_no_se_pisa():
    avisos = verificar_no_pisar(registro(), estado_pagina=0,
                               sobrescribir=False)
    assert [a.codigo for a in avisos] == ["ya_indexada"]


def test_pagina_ya_valida_se_pisa_si_se_pide():
    assert verificar_no_pisar(registro(), 0, sobrescribir=True) == []


def test_pagina_amarilla_se_puede_escribir():
    assert verificar_no_pisar(registro(), 3, sobrescribir=False) == []


def test_logs_repetidos_se_acusan():
    avisos = verificar_duplicados([registro(1, log="2287325"),
                                   registro(2, log="2287325")])
    assert [a.codigo for a in avisos] == ["log_duplicado"]
    assert avisos[0].seq == 2


def test_logs_vacios_no_cuentan_como_duplicados():
    assert verificar_duplicados([registro(1, log=""),
                                 registro(2, log="")]) == []

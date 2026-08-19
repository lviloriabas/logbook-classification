"""Ubicar el lote por nombre, sin adivinar cuando hay dudas."""

from __future__ import annotations

import pytest

from app.airvault.discovery import (
    LoteAmbiguo,
    LoteNoEncontrado,
    buscar,
    buscar_por_id,
    esperar,
    normalizar_nombre,
)
from tests.airvault_fake import lote


def test_nombre_exacto():
    lotes = [lote("003AAA", "DP | BITS VARIAS 24", 20),
             lote("003BBB", "DP | BITS VARIAS 25", 20)]
    assert buscar(lotes, "DP | BITS VARIAS 24").batch_id == "003AAA"


def test_nombre_no_distingue_mayusculas_ni_separadores():
    lotes = [lote("003AAA", "DP | BITS VARIAS 24", 20)]
    assert buscar(lotes, "dp - bits varias 24").batch_id == "003AAA"


def test_sufijo_de_quick_upload_no_estorba():
    # Quick Upload deja el nombre como "<lote> - usuario@dominio".
    lotes = [lote("003AAA", "DP | BITS VARIAS 24 - luis@copaair.com", 20)]
    assert buscar(lotes, "DP | BITS VARIAS 24").batch_id == "003AAA"


def test_nombre_inexistente():
    with pytest.raises(LoteNoEncontrado):
        buscar([lote("003AAA", "otro lote", 5)], "DP | BITS VARIAS 24")


def test_nombre_vacio():
    with pytest.raises(LoteNoEncontrado):
        buscar([lote("003AAA", "x", 1)], "")


def test_empate_se_resuelve_por_paginas():
    lotes = [lote("003AAA", "DP | BITS VARIAS 24", 20),
             lote("003BBB", "DP | BITS VARIAS 24", 55)]
    assert buscar(lotes, "DP | BITS VARIAS 24",
                  paginas_esperadas=55).batch_id == "003BBB"


def test_empate_sin_desempate_no_adivina():
    lotes = [lote("003AAA", "DP | BITS VARIAS 24", 20),
             lote("003BBB", "DP | BITS VARIAS 24", 20)]
    with pytest.raises(LoteAmbiguo):
        buscar(lotes, "DP | BITS VARIAS 24", paginas_esperadas=20)


def test_repositorio_distinto_no_cuenta():
    lotes = [lote("003AAA", "DP | BITS VARIAS 24", 20, repo_id=999)]
    with pytest.raises(LoteNoEncontrado):
        buscar(lotes, "DP | BITS VARIAS 24", repo_id=3209)


def test_exacta_gana_a_la_del_sufijo():
    lotes = [lote("003AAA", "DP | BITS VARIAS 24 - luis@copaair.com", 20),
             lote("003BBB", "DP | BITS VARIAS 24", 20)]
    assert buscar(lotes, "DP | BITS VARIAS 24").batch_id == "003BBB"


def test_esperar_reintenta_hasta_que_aparece():
    intentos = {"n": 0}
    dormidas = []

    def listar():
        intentos["n"] += 1
        if intentos["n"] < 3:
            return []
        return [lote("003AAA", "DP | BITS VARIAS 24", 20)]

    reloj = iter([0.0, 0.0, 20.0, 40.0, 60.0, 80.0])
    encontrado = esperar(
        listar, "DP | BITS VARIAS 24", espera_s=20, limite_s=900,
        dormir=dormidas.append, reloj=lambda: next(reloj),
    )
    assert encontrado.batch_id == "003AAA"
    assert dormidas == [20, 20]


def test_esperar_se_rinde_al_vencer_el_limite():
    reloj = iter([0.0, 100.0, 200.0])
    with pytest.raises(LoteNoEncontrado):
        esperar(lambda: [], "DP | BITS VARIAS 24", espera_s=1, limite_s=50,
                dormir=lambda _s: None, reloj=lambda: next(reloj))


def test_esperar_no_insiste_si_es_ambiguo():
    # Esperar no va a resolver una ambiguedad; hay que preguntar.
    lotes = [lote("003AAA", "x", 5), lote("003BBB", "x", 5)]
    with pytest.raises(LoteAmbiguo):
        esperar(lambda: lotes, "x", espera_s=1, limite_s=50,
                dormir=lambda _s: None, reloj=lambda: 0.0)


def test_buscar_por_id():
    lotes = [lote("003AAA", "x", 5)]
    assert buscar_por_id(lotes, "003aaa").batch_id == "003AAA"
    assert buscar_por_id(lotes, "003ZZZ") is None


def test_normalizar_nombre():
    assert normalizar_nombre("  DP | BITS  VARIAS_24 ") == "dp bits varias 24"


def test_esperar_filtra_del_lado_del_servidor():
    """El filtro 'Filter by' es una subcadena sin distinguir mayusculas."""
    from tests.airvault_fake import ClienteFalso

    cliente = ClienteFalso(lotes=[
        lote("003AAA", "DP | Bitácoras varias 4", 472),
        lote("003BBB", "DP | BIT 18 AUG 2026 05 42", 20),
        lote("003CCC", "F&A SHOP VISIT-Batch", 9),
    ])
    encontrado = esperar(
        cliente.listar_lotes, "DP | BIT 18 AUG 2026 05 42",
        espera_s=1, limite_s=10, dormir=lambda _s: None, reloj=lambda: 0.0,
    )
    assert encontrado.batch_id == "003BBB"
    assert cliente.filtros == ["DP | BIT 18 AUG 2026 05 42"]


def test_el_filtro_dp_bit_atrapa_tambien_bitacoras():
    """Por eso el nombre lleva marca de tiempo: 'DP | BIT' solo no basta."""
    from tests.airvault_fake import ClienteFalso

    cliente = ClienteFalso(lotes=[
        lote("003AAA", "DP | Bitácoras varias 4", 472),
        lote("003BBB", "DP | BITS VARIAS 19", 393),
        lote("003CCC", "DP | BIT Mix | Viernes 14 AUG", 109),
    ])
    assert len(cliente.listar_lotes("DP | BIT")) == 3


def test_nombre_con_acentos_coincide_aunque_venga_escapado():
    lotes = [lote("003AAA", "DP | Bit&#225;coras varias 4", 472)]
    assert buscar(lotes, "DP | Bitácoras varias 4").batch_id == "003AAA"

"""Ubicar el lote, sin adivinar cuando hay dudas.

Por nombre cuando alguien lo subio a mano poniendoselo, y por lo que
aparecio despues de subir cuando lo sube el programa: Quick Upload no
admite nombre de lote y la cola los recibe todos como «Empty-Batch».
"""

from __future__ import annotations

import pytest

from app.airvault.discovery import (
    LoteAmbiguo,
    LoteNoEncontrado,
    buscar,
    buscar_nuevo,
    buscar_por_id,
    esperar,
    normalizar_nombre,
    recien_llegados,
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


# ── por lo que aparecio despues de subir ───────────────────────────

def test_el_lote_propio_es_el_que_no_estaba_antes():
    """El nombre no distingue nada: todos llegan como «Empty-Batch»."""
    antes = ["003AAA", "003BBB"]
    ahora = [lote("003AAA", "Empty-Batch", 12),
             lote("003BBB", "Empty-Batch", 30),
             lote("003CCC", "Empty-Batch", 29)]
    assert buscar_nuevo(ahora, antes).batch_id == "003CCC"


def test_mientras_no_aparezca_nada_no_es_un_fallo():
    """El servidor tarda en procesar lo subido; esperar es lo correcto."""
    antes = ["003AAA"]
    assert buscar_nuevo([lote("003AAA", "Empty-Batch", 12)], antes) is None


def test_con_varios_nuevos_desempata_la_cantidad_de_paginas():
    antes: list = []
    ahora = [lote("003BBB", "Empty-Batch", 4),
             lote("003CCC", "Empty-Batch", 29)]
    assert buscar_nuevo(ahora, antes, paginas_esperadas=29).batch_id == "003CCC"


def test_si_no_hay_forma_de_desempatar_no_se_adivina():
    """Escribir en el lote equivocado es peor que pedir el batch id."""
    ahora = [lote("003BBB", "Empty-Batch", 29),
             lote("003CCC", "Empty-Batch", 29)]
    with pytest.raises(LoteAmbiguo):
        buscar_nuevo(ahora, [], paginas_esperadas=29)


def test_los_de_otro_repositorio_no_cuentan():
    """En la cola conviven repositorios; el de al lado no es el nuestro."""
    ahora = [lote("003BBB", "Empty-Batch", 29, repo_id=1),
             lote("003CCC", "Empty-Batch", 29, repo_id=3209)]
    assert buscar_nuevo(ahora, [], repo_id=3209).batch_id == "003CCC"


def test_recien_llegados_no_distingue_mayusculas_en_el_id():
    ahora = [lote("003aaa", "Empty-Batch", 12),
             lote("003CCC", "Empty-Batch", 29)]
    assert [l.batch_id for l in recien_llegados(ahora, ["003AAA"])] == ["003CCC"]


def test_esperando_se_cae_al_recien_llegado_cuando_el_nombre_no_sirve():
    """Es el caso real: se sube, y lo que llega no se llama como se pidio."""
    cola = [lote("003AAA", "Empty-Batch", 12)]

    def listar(filtro=""):
        # El filtro del servidor por nombre no devuelve nada, claro.
        return [] if filtro else list(cola)

    cola.append(lote("003CCC", "Empty-Batch", 29))
    encontrado = esperar(
        listar, "DP | BITS 19 AUG 2026 17 33", None, 29,
        dormir=lambda _s: None, previos=["003AAA"],
    )
    assert encontrado.batch_id == "003CCC"

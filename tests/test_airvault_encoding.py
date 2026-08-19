"""La codificacion tiene que producir exactamente lo que espera el servidor."""

from __future__ import annotations

import base64

from app.airvault.encoding import (
    codificar_batch_id,
    codificar_sticky,
    codificar_valores,
    decodificar_valores,
)


def test_batch_id_conocido():
    # Vector tomado de una peticion real del Web Index.
    assert codificar_batch_id("003SRO") == "MDAzU1JP"


def test_valores_van_separados_por_tabulador():
    encoded = codificar_valores({9586: "Log Page", 9633: "HP-1848CMP"})
    crudo = base64.b64decode(encoded).decode("utf-8")
    assert crudo == "9586=Log Page\t9633=HP-1848CMP"


def test_orden_estable_entre_llamadas():
    valores = {9586: "Log Page", 9633: "HP-1848CMP", 9675: "2287325"}
    assert codificar_valores(valores) == codificar_valores(dict(valores))


def test_valor_vacio_viaja_como_vacio():
    crudo = base64.b64decode(codificar_valores({9593: ""})).decode("utf-8")
    assert crudo == "9593="


def test_none_no_escribe_la_palabra_none():
    crudo = base64.b64decode(
        codificar_valores({9593: None})  # type: ignore[dict-item]
    ).decode("utf-8")
    assert crudo == "9593="


def test_ida_y_vuelta():
    valores = {9586: "Log Page", 9675: "2287325", 9593: "08/31/2026"}
    assert decodificar_valores(codificar_valores(valores)) == {
        k: str(v) for k, v in valores.items()
    }


def test_sticky_vacio_es_cadena_vacia():
    # El servidor espera "" y no el base64 de una cadena vacia.
    assert codificar_sticky([]) == ""


def test_sticky_con_campos():
    crudo = base64.b64decode(codificar_sticky([9586, 9633])).decode("utf-8")
    assert crudo == "9586\t9633"


def test_decodificar_vacio():
    assert decodificar_valores("") == {}

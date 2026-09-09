"""Categorias exactas, multiples faltas y conservacion de anotaciones."""

import pytest

from app.airvault.ecn import CAMPOS_ECN, RAZON_POR_CAMPO, conservar_razones, razones_ecn
from app.airvault.indexer import Indexador, verificar_revision
from app.airvault.ecn import campos_del_resumen
from app.airvault.mapping import registros_desde_entrega, valores_de_indice
from tests.airvault_fake import ClienteFalso, pagina
from tests.test_airvault_indexer import manifiesto, PICKLIST


def test_capitan_comparte_categoria_y_tecnico_tiene_dos():
    assert len(razones_ecn(["captain_signature", "captain_license"])) == 1
    assert razones_ecn(["technician_signature", "technician_license"]) == [
        "DISCREPANCY NOTE: MISSING TECHNICIAN SIGNATURE",
        "DISCREPANCY NOTE: MISSING LICENSE NUMBER",
    ]
    assert razones_ecn(["correction_block", "desconocido"]) == []


def test_guarda_tres_razones_y_confirma_el_final():
    m = manifiesto(1)
    m.solo_subir = True
    r = m.registros[0]
    r.discrepancia = True
    r.discrepancy_fields = ["pilot_signature", "technician_signature", "technician_license"]
    cli = ClienteFalso(page_count=1)
    indexador = Indexador(cli, m, PICKLIST)
    assert indexador.aplicar(indexador.planificar(1)).escritas == 1
    valores = cli.escrituras[0][1]
    assert [valores[c] for c in CAMPOS_ECN] == razones_ecn(r.discrepancy_fields)
    assert verificar_revision(cli, m) == (1, 1, [])


def test_preserva_razon_manual_y_evitar_duplicarla():
    razon = RAZON_POR_CAMPO["pilot_signature"]
    assert conservar_razones({9692: razon}, {9692: "manual"}) == {9692: "manual", 9781: razon}
    assert conservar_razones({9692: razon}, {9782: razon}) == {9782: razon}
    with pytest.raises(ValueError, match="ocupados"):
        conservar_razones({9692: razon}, {c: "manual" for c in CAMPOS_ECN})


def test_no_pisa_categorias_manual_al_verificar():
    m = manifiesto(1)
    m.solo_subir = True
    m.registros[0].discrepancia = True
    m.registros[0].discrepancy_fields = ["captain_license"]
    cli = ClienteFalso(paginas={1: pagina(1, valores={9692: "manual"})})
    indexador = Indexador(cli, m, PICKLIST)
    assert indexador.aplicar(indexador.planificar(1)).escritas == 1
    assert cli.escrituras[0][1][9692] == "manual"
    assert cli.escrituras[0][1][9781] == RAZON_POR_CAMPO["captain_license"]
    assert verificar_revision(cli, m) == (1, 1, [])


def test_entrega_anterior_conserva_razones_del_resumen_sin_cambiar_csv():
    filas = [{"file": "x.pdf", "page": "1", "disc": "true",
              "disc_reason": "Faltan firma de capitán y licencia de capitán"}]
    registro = registros_desde_entrega(filas, [{"archivo": "x.pdf", "pagina": 1}])[0]
    assert valores_de_indice(registro, "Log Page", "PUBLISHED")[9692] == RAZON_POR_CAMPO["captain_license"]
    assert campos_del_resumen("Corrección escrita: falta licencia de técnico") == ["technician_license"]
    assert campos_del_resumen("No falta firma de piloto") == []
    assert campos_del_resumen("Falta firma de piloto; incierta") == []

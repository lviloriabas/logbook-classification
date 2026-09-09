"""Los datos legibles se guardan y solo lo pendiente requiere una persona."""

import pytest

from app.airvault.config import (
    CAMPOS_OBLIGATORIOS, CAMPO_END_DATE, CAMPO_FLEET, CAMPO_LOG_NUMBER,
    CAMPO_MATRICULA, ESTADO_NECESITA_CORRECCION, ESTADO_VALIDO,
)
from app.airvault.guards import verificar_obligatorios
from app.airvault.indexer import Indexador
from app.airvault.indexer import verificar_revision
from app.airvault.mapping import registros_desde_entrega
from app.airvault.model import EstadoRegistro
from tests.airvault_fake import ClienteFalso, pagina
from tests.test_airvault_indexer import manifiesto, PICKLIST


@pytest.mark.parametrize("campo,atributo", [
    (CAMPO_END_DATE, "fecha"), (CAMPO_MATRICULA, "matricula"),
    (CAMPO_FLEET, "fleet"),
])
def test_guarda_logpage_aunque_falte_otro_obligatorio(campo, atributo):
    m = manifiesto(1)
    m.solo_subir = True
    setattr(m.registros[0], atributo, "")
    cli = ClienteFalso(page_count=1)
    indexador = Indexador(cli, m, PICKLIST)
    resultado = indexador.aplicar(indexador.planificar(1))
    assert (resultado.escritas, resultado.fallidas) == (1, 0)
    _, valores, estado = cli.escrituras[0]
    assert valores[CAMPO_LOG_NUMBER] == "2287321"
    assert campo not in valores
    assert all(valores[c] for c in CAMPOS_OBLIGATORIOS if c in valores)
    assert estado == ESTADO_NECESITA_CORRECCION
    assert cli.lecturas == [1, 1]


def test_permitir_parcial_no_autoriza_enviar_un_vacio_explicito():
    r = manifiesto(1).registros[0]
    avisos = verificar_obligatorios(r, {CAMPO_LOG_NUMBER: ""}, permitir_incompletos=True)
    assert any(a.codigo == "obligatorio_vacio" and "Log Page Number" in a.detalle
               for a in avisos)


@pytest.mark.parametrize("pendiente,discrepancia,esperado", [
    (False, False, ESTADO_VALIDO),
    (True, False, ESTADO_NECESITA_CORRECCION),
    (False, True, ESTADO_NECESITA_CORRECCION),
    (None, False, ESTADO_NECESITA_CORRECCION),
])
def test_decide_por_pagina_y_conserva_motivos_antiguos(pendiente, discrepancia, esperado):
    m = manifiesto(1)
    m.solo_subir = True
    m.registros[0].revision_pendiente = pendiente
    m.registros[0].discrepancia = discrepancia
    cli = ClienteFalso(page_count=1)
    indexador = Indexador(cli, m, PICKLIST)
    assert indexador.aplicar(indexador.planificar(1)).escritas == 1
    assert cli.escrituras[0][2] == esperado
    assert not cli.completados


def test_la_correspondencia_incorrecta_sigue_bloqueando_el_guardado_parcial():
    m = manifiesto(1)
    m.solo_subir = True
    m.registros[0].fecha = ""
    cli = ClienteFalso(paginas={1: pagina(1, valores={CAMPO_LOG_NUMBER: "9999999"})})
    indexador = Indexador(cli, m, PICKLIST)
    assert not indexador.planificar(1).escribibles
    assert not cli.escrituras


def test_no_da_por_guardada_una_pagina_si_el_servidor_perdio_el_log():
    class PierdeLog(ClienteFalso):
        def guardar_pagina(self, *args):
            resultado = super().guardar_pagina(*args)
            if args[1] == 1:
                self.paginas[1].valores.pop(CAMPO_LOG_NUMBER)
            return resultado

    m = manifiesto(2)
    m.solo_subir = True
    cli = PierdeLog(page_count=2)
    indexador = Indexador(cli, m, PICKLIST)
    resultado = indexador.aplicar(indexador.planificar(2))
    assert (resultado.escritas, resultado.fallidas) == (1, 1)
    assert m.registros[0].estado == EstadoRegistro.ERROR
    assert m.registros[1].estado == EstadoRegistro.ESCRITA


@pytest.mark.parametrize("marca,esperado", [(False, False), (True, True), (None, None), ("false", None)])
def test_la_causa_de_revision_viaja_del_indice_al_manifiesto(marca, esperado):
    registros = registros_desde_entrega(
        [{"file": "x.pdf", "page": "1", "log_number": "2287321",
          "matricula": "HP-1848CMP", "date": "2026/08/31"}],
        [{"archivo": "x.pdf", "pagina": 1, "revision_pendiente": marca}],
    )
    assert registros[0].revision_pendiente is esperado


def test_revision_termina_con_datos_parciales_y_no_los_reescribe():
    m = manifiesto(2)
    m.solo_subir = True
    m.registros[0].fecha = ""
    m.registros[1].discrepancia = True
    cli = ClienteFalso(page_count=2)
    indexador = Indexador(cli, m, PICKLIST)
    assert indexador.aplicar(indexador.planificar(2)).escritas == 2
    assert verificar_revision(cli, m) == (2, 2, [])
    assert indexador.aplicar(indexador.planificar(2)).escritas == 0
    assert len(cli.escrituras) == 2
    assert not cli.completados


def test_verificar_revision_no_confirma_datos_que_se_perdieron():
    m = manifiesto(1)
    m.solo_subir = True
    cli = ClienteFalso(page_count=1)
    indexador = Indexador(cli, m, PICKLIST)
    indexador.aplicar(indexador.planificar(1))
    cli.paginas[1].valores[CAMPO_LOG_NUMBER] = "9999999"
    confirmadas, total, problemas = verificar_revision(cli, m)
    assert (confirmadas, total) == (0, 1)
    assert "Log Page Number" in problemas[0]


def test_una_revision_resuelta_pasa_de_amarillo_a_valido():
    m = manifiesto(1)
    m.solo_subir = True
    cli = ClienteFalso(page_count=1)
    indexador = Indexador(cli, m, PICKLIST)
    indexador.aplicar(indexador.planificar(1))
    m.registros[0].revision_pendiente = False
    assert indexador.aplicar(indexador.planificar(1)).escritas == 1
    assert cli.paginas[1].estado == ESTADO_VALIDO


def test_trabajo_terminado_persiste_y_sale_de_la_busqueda(tmp_path):
    from app.airvault.config import AirVaultConfig
    from app.airvault.flujo import Trabajo, estado_local, INDEXADO
    from app.airvault.model import EstadoEtapa

    m = manifiesto(1)
    m.solo_subir = True
    m.registros[0].fecha = ""
    cli = ClienteFalso(page_count=1)
    indexador = Indexador(cli, m, PICKLIST)
    indexador.aplicar(indexador.planificar(1))
    trabajo = Trabajo(AirVaultConfig(), tmp_path, m)
    m.etapa("subir").marcar(EstadoEtapa.HECHA)
    avances = []
    assert trabajo.verificar(cli, al_avanzar=lambda n, t: avances.append((n, t))) == (1, 1, [])
    assert m.etapa("verificar").estado is EstadoEtapa.HECHA
    assert estado_local(trabajo).estado == INDEXADO
    assert avances == [(0, 1), (1, 1)]
    assert not cli.completados


def test_rechazo_de_obligatorio_omitido_no_finge_exito_ni_corta_las_demas():
    from app.airvault.session import ErrorDeAirVault

    class ExigeAircraft(ClienteFalso):
        def guardar_pagina(self, batch, pagina, valores, estado, siguiente=None):
            if CAMPO_MATRICULA not in valores:
                raise ErrorDeAirVault("Field Aircraft value is required")
            return super().guardar_pagina(batch, pagina, valores, estado, siguiente)

    m = manifiesto(2)
    m.solo_subir = True
    m.registros[0].matricula = ""
    cli = ExigeAircraft(page_count=2)
    indexador = Indexador(cli, m, PICKLIST)
    resultado = indexador.aplicar(indexador.planificar(2))
    assert (resultado.escritas, resultado.fallidas) == (1, 1)
    assert verificar_revision(cli, m)[:2] == (1, 2)

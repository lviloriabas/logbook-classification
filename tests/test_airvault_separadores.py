"""Los separadores del PDF ocupan página en el batch y no se indexan.

El PDF que se sube lleva páginas divisorias que el CSV no tiene. Cuentan
para la correspondencia por posición —en AirVault son una página más— pero
no son documentos: nadie les escribe matrícula, ni log number, ni fecha.
"""

from __future__ import annotations

import json

import pytest

from app.airvault.config import (
    AirVaultConfig, CAMPO_LOG_NUMBER, CAMPO_MATRICULA,
)
from app.airvault.flujo import Trabajo, ruta_indice_paginas
from app.airvault.guards import ErrorDeGuarda, verificar_cantidad
from app.airvault.indexer import Indexador
from app.airvault.mapping import leer_indice_paginas, registros_desde_entrega
from app.airvault.model import EstadoRegistro
from tests.airvault_fake import ClienteFalso, lote, pagina

CSV_FILAS = [
    {"file": "Image_001.pdf", "page": "1", "log_number": "2312238",
     "matricula": "HP-1848CMP", "date": "2026/08/12", "dup": "false",
     "disc": "false"},
    {"file": "Image_001.pdf", "page": "2", "log_number": "2312239",
     "matricula": "HP-1848CMP", "date": "2026/08/13", "dup": "false",
     "disc": "false"},
    {"file": "Image_001.pdf", "page": "3", "log_number": "2312240",
     "matricula": "", "date": "", "dup": "false", "disc": "false"},
]

# Lo que deja el PDF: la sección del avión, y al final las que nadie pudo
# asignar bajo el separador REVISAR.
INDICE = [
    {"separador": "HP-1848CMP"},
    {"archivo": "Image_001.pdf", "pagina": 1},
    {"archivo": "Image_001.pdf", "pagina": 2},
    {"separador": "REVISAR"},
    {"archivo": "Image_001.pdf", "pagina": 3},
]


def registros():
    return registros_desde_entrega(CSV_FILAS, INDICE)


# ── el manifiesto sigue al PDF, no al CSV ──────────────────────────

def test_el_separador_ocupa_su_pagina():
    """Sin contarlos, todo lo que va detras se escribiria una pagina corrido."""
    obtenidos = registros()
    assert [r.seq for r in obtenidos] == [1, 2, 3, 4, 5]
    assert [r.separador for r in obtenidos] == [
        "HP-1848CMP", "", "", "REVISAR", ""
    ]


def test_la_bitacora_cae_en_la_pagina_que_le_toca():
    obtenidos = registros()
    assert obtenidos[1].log_number == "2312238"
    assert obtenidos[2].log_number == "2312239"
    assert obtenidos[4].log_number == "2312240"


def test_el_separador_no_lleva_datos_de_bitacora():
    separador = registros()[0]
    assert separador.es_separador
    assert (separador.matricula, separador.log_number, separador.fecha) == (
        "", "", ""
    )
    assert not separador.listo_para_escribir()


def test_una_pagina_del_pdf_que_no_esta_en_el_csv_queda_avisada():
    obtenidos = registros_desde_entrega(
        CSV_FILAS, INDICE + [{"archivo": "Image_001.pdf", "pagina": 99}]
    )
    assert obtenidos[-1].avisos and "sin_fila" in obtenidos[-1].avisos[0]
    assert not obtenidos[-1].listo_para_escribir()


def test_el_indice_se_lee_del_archivo_de_la_corrida(tmp_path):
    ruta = tmp_path / "corrida_paginas.json"
    ruta.write_text(
        json.dumps({"version": 2, "partes": [
            {"pdf": "ejecución.pdf", "paginas": INDICE}
        ]}),
        encoding="utf-8",
    )
    partes = leer_indice_paginas(ruta)
    assert len(partes) == 1 and partes[0]["paginas"] == INDICE


def test_un_indice_de_la_primera_version_se_sigue_leyendo(tmp_path):
    """Ejecuciones exportadas antes de que la entrega pudiera repartirse."""
    ruta = tmp_path / "corrida_paginas.json"
    ruta.write_text(
        json.dumps({"version": 1, "pdf": "ejecución.pdf", "paginas": INDICE}),
        encoding="utf-8",
    )
    partes = leer_indice_paginas(ruta)
    assert len(partes) == 1 and partes[0]["paginas"] == INDICE


def test_sin_indice_no_se_inventa_nada(tmp_path):
    assert leer_indice_paginas(tmp_path / "no-existe.json") == []
    roto = tmp_path / "roto.json"
    roto.write_text("{ esto no es json", encoding="utf-8")
    assert leer_indice_paginas(roto) == []


# ── la cuenta del batch incluye los separadores ─────────────────────

def test_el_lote_tiene_una_pagina_por_separador():
    verificar_cantidad(registros(), 5)


def test_contar_solo_las_bitacoras_detiene_el_trabajo():
    with pytest.raises(ErrorDeGuarda) as fallo:
        verificar_cantidad(registros(), 3)
    assert "separadores" in str(fallo.value)


# ── el indexador automatico los borra ──────────────────────────────

def manifiesto_con_separadores(tmp_path):
    from app.airvault.model import Manifiesto

    return Manifiesto(
        job_id="x", nombre_batch="DP | BIT", batch_id="003SRO",
        registros=registros(),
    )


def test_no_se_escribe_en_las_paginas_divisorias(tmp_path):
    cliente = ClienteFalso(
        paginas={n: pagina(n, estado=3) for n in range(1, 6)},
        picklist=["HP-1848CMP"], page_count=5,
    )
    indexador = Indexador(cliente, manifiesto_con_separadores(tmp_path),
                          ["HP-1848CMP"])
    plan = indexador.planificar(5)
    indexador.aplicar(plan)

    escritas = [p for p, _v, _e in cliente.escrituras]
    assert 1 not in escritas and 4 not in escritas
    assert escritas == [2, 3, 5]
    assert cliente.borradas == [1, 4]


def test_la_divisoria_ni_siquiera_se_lee(tmp_path):
    """Leerlas serian cientos de peticiones de mas contra el servidor."""
    cliente = ClienteFalso(
        paginas={n: pagina(n, estado=3) for n in range(1, 6)},
        picklist=["HP-1848CMP"], page_count=5,
    )
    Indexador(cliente, manifiesto_con_separadores(tmp_path),
              ["HP-1848CMP"]).planificar(5)
    assert sorted(cliente.lecturas) == [2, 3, 5]


def test_el_separador_no_cuenta_como_omitido(tmp_path):
    cliente = ClienteFalso(
        paginas={n: pagina(n, estado=3) for n in range(1, 6)},
        picklist=["HP-1848CMP"], page_count=5,
    )
    manifiesto = manifiesto_con_separadores(tmp_path)
    indexador = Indexador(cliente, manifiesto, ["HP-1848CMP"])
    plan = indexador.planificar(5)
    resultado = indexador.aplicar(plan)

    assert resultado.escritas == 3
    # La pagina 5 no tiene matricula, pero recibe los demas datos y queda
    # amarilla. Los separadores no son escritos ni omitidos porque nunca
    # hubo nada que escribir en ellos.
    assert resultado.omitidas == 0
    assert resultado.separadores_borrados == 2
    assert resultado.separadores_pendientes == 0
    assert plan.resumen()["separadores"] == 2


def test_revisar_conserva_separadores_y_escribe_los_datos_disponibles(tmp_path):
    cliente = ClienteFalso(page_count=5)
    manifiesto = manifiesto_con_separadores(tmp_path)
    manifiesto.solo_subir = True
    indexador = Indexador(cliente, manifiesto, ["HP-1848CMP"])

    resultado = indexador.aplicar(indexador.planificar(5))

    assert resultado.separadores_borrados == 0
    assert cliente.borradas == []
    assert [pagina for pagina, _valores, _estado in cliente.escrituras] == [
        2,
        3,
        5,
    ]


def test_si_airvault_no_deja_borrar_un_separador_se_informa(tmp_path):
    cliente = ClienteFalso(
        paginas={n: pagina(n, estado=3) for n in range(1, 6)},
        picklist=["HP-1848CMP"], page_count=5,
        no_se_pueden_borrar={4},
    )
    indexador = Indexador(cliente, manifiesto_con_separadores(tmp_path),
                          ["HP-1848CMP"])

    resultado = indexador.aplicar(indexador.planificar(5))

    assert resultado.separadores_borrados == 1
    assert resultado.separadores_pendientes == 1
    assert cliente.borradas == [1]
    assert any("pagina 4" in detalle for detalle in resultado.detalles)


def test_la_matricula_vacia_de_un_separador_no_es_un_aviso(tmp_path):
    """Sin saltarlos, cada divisoria abriria un aviso de matricula vacia."""
    cliente = ClienteFalso(
        paginas={n: pagina(n, estado=3) for n in range(1, 6)},
        picklist=["HP-1848CMP"], page_count=5,
    )
    plan = Indexador(cliente, manifiesto_con_separadores(tmp_path),
                     ["HP-1848CMP"]).planificar(5)
    divisorias = [p for p in plan.paginas if p.registro.es_separador]
    assert len(divisorias) == 2
    assert all(not p.avisos for p in divisorias)


def test_la_verificacion_no_espera_que_una_divisoria_sea_valida(tmp_path):
    """En AirVault un separador queda en estado Separator, no en Valid."""
    from app.airvault.indexer import verificar_lote

    manifiesto = manifiesto_con_separadores(tmp_path)
    for registro in manifiesto.registros:
        if not registro.es_separador:
            registro.estado = EstadoRegistro.ESCRITA
    cliente = ClienteFalso(paginas={
        1: pagina(1, estado=2),
        2: pagina(2, estado=0, valores={
            CAMPO_LOG_NUMBER: "2312238", CAMPO_MATRICULA: "HP-1848CMP",
        }),
        3: pagina(3, estado=0, valores={
            CAMPO_LOG_NUMBER: "2312239", CAMPO_MATRICULA: "HP-1848CMP",
        }),
        4: pagina(4, estado=2),
        5: pagina(5, estado=0, valores={
            CAMPO_LOG_NUMBER: "2312240", CAMPO_MATRICULA: "",
        }),
    }, page_count=5)
    validas, total, problemas = verificar_lote(cliente, manifiesto)
    assert (validas, total) == (3, 3)
    assert problemas == []


# ── de punta a punta con el indice de la ejecución ───────────────────

def test_el_trabajo_toma_el_orden_del_pdf(tmp_path):
    carpeta = tmp_path / "output" / "BITS 18 AUG 2026 05 42"
    (carpeta / "datos").mkdir(parents=True)
    csv = carpeta / "datos" / "BITS 18 AUG 2026 05 42.CSV"
    csv.write_text(
        "file,page,log_number,dup,disc,matricula,date\n"
        "Image_001.pdf,1,2312238,false,false,HP-1848CMP,2026/08/12\n"
        "Image_001.pdf,2,2312239,false,false,HP-1848CMP,2026/08/13\n",
        encoding="utf-8",
    )
    ruta_indice_paginas(csv).write_text(
        json.dumps({"version": 1, "paginas": [
            {"separador": "HP-1848CMP"},
            {"archivo": "Image_001.pdf", "pagina": 1},
            {"archivo": "Image_001.pdf", "pagina": 2},
        ]}),
        encoding="utf-8",
    )
    trabajo = Trabajo.preparar(AirVaultConfig(), tmp_path / "job", csv)

    assert len(trabajo.manifiesto.registros) == 3
    assert len(trabajo.manifiesto.bitacoras()) == 2
    assert trabajo.manifiesto.registros[0].es_separador
    assert "1 separadores" in trabajo.manifiesto.etapa("preparar").detalle


def test_sin_indice_se_sigue_el_csv_y_la_guarda_protege(tmp_path):
    """Ejecuciones viejas: si el PDF traia separadores, no se escribe nada."""
    carpeta = tmp_path / "output" / "BITS 18 AUG 2026 05 42"
    (carpeta / "datos").mkdir(parents=True)
    csv = carpeta / "datos" / "BITS 18 AUG 2026 05 42.CSV"
    csv.write_text(
        "file,page,log_number,dup,disc,matricula,date\n"
        "Image_001.pdf,1,2312238,false,false,HP-1848CMP,2026/08/12\n",
        encoding="utf-8",
    )
    trabajo = Trabajo.preparar(AirVaultConfig(), tmp_path / "job", csv)
    assert len(trabajo.manifiesto.registros) == 1
    assert "sin indice" in trabajo.manifiesto.etapa("preparar").detalle

    trabajo.fijar_lote("003SRO")
    cliente = ClienteFalso(paginas={1: pagina(1), 2: pagina(2)}, page_count=2)
    with pytest.raises(ErrorDeGuarda):
        trabajo.planificar(cliente)
    assert cliente.escrituras == []

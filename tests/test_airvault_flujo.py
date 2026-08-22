"""Recorrido de punta a punta: CSV de la ejecución hasta batch indexado.

Es el test que amarra las piezas. Usa el CSV tal como lo escribe la
ejecución y un batch falso, de modo que cubre el camino completo sin red.
"""

from __future__ import annotations

from app.airvault import manifest as manifiestos
from app.airvault.config import CAMPO_LOG_NUMBER, CAMPO_MATRICULA
from app.airvault.discovery import buscar
from app.airvault.indexer import Indexador, verificar_lote
from app.airvault.mapping import (
    ResolutorFlota,
    leer_csv_corrida,
    registros_desde_csv,
)
from app.airvault.model import EstadoEtapa, EstadoRegistro, Manifiesto
from app.airvault.report import escribir_csv
from tests.airvault_fake import ClienteFalso, lote, pagina

CSV = (
    "﻿file,page,log_number,dup,disc,matricula,flight_number,"
    "pilot_signature,captain_signature,captain_license,"
    "technician_signature,date,time_ms\n"
    "Image_001.pdf,1,2312238,false,false,HP-1848CMP,SYZ,true,false,false,"
    "true,2026/08/12,10372.0\n"
    "Image_001.pdf,2,2312239,false,true,HP-1848CMP,389,true,true,true,"
    "false,2026/08/31,11268.3\n"
    "Image_001.pdf,3,,false,false,,,false,false,false,false,,900.0\n"
    "Image_001.pdf,4,2312240,false,false,HP-9912CMP,125,true,true,true,"
    "false,2026/08/11,9755.8\n"
)


def _preparar(tmp_path):
    csv_path = tmp_path / "BITS.CSV"
    csv_path.write_text(CSV, encoding="utf-8")
    resolutor = ResolutorFlota({"HP-1848CMP": {"fleet": "NG",
                                               "lessor": "SMBC A.C"}})
    registros = registros_desde_csv(leer_csv_corrida(csv_path), resolutor)
    manifiesto = Manifiesto(
        job_id="prueba", nombre_batch="DP | PRUEBA 1",
        csv_origen=str(csv_path), registros=registros,
    )
    manifiesto.etapa("preparar").marcar(EstadoEtapa.HECHA)
    manifiestos.guardar(manifiesto, tmp_path / "job")
    return manifiesto


def test_la_pagina_en_blanco_no_llega_al_lote(tmp_path):
    manifiesto = _preparar(tmp_path)
    assert len(manifiesto.registros) == 3
    assert [r.log_number for r in manifiesto.registros] == [
        "2312238", "2312239", "2312240",
    ]


def test_flujo_completo(tmp_path):
    carpeta = tmp_path / "job"
    manifiesto = _preparar(tmp_path)

    # descubrir
    lotes = [lote("003AAA", "otro", 10),
             lote("003BBB", "DP | PRUEBA 1", 3)]
    encontrado = buscar(lotes, manifiesto.nombre_batch,
                        paginas_esperadas=len(manifiesto.registros))
    manifiesto.batch_id = encontrado.batch_id
    manifiesto.etapa("descubrir").marcar(EstadoEtapa.HECHA)
    manifiestos.guardar(manifiesto, carpeta)

    # planificar sobre un batch que ya trae los log numbers del preindexado
    cliente = ClienteFalso(
        paginas={
            1: pagina(1, valores={CAMPO_LOG_NUMBER: "2312238"}),
            2: pagina(2, valores={CAMPO_LOG_NUMBER: "2312239"}),
            3: pagina(3, valores={CAMPO_LOG_NUMBER: "2312240"}),
        },
        page_count=3,
        picklist=["HP-1848CMP", "HP-9912CMP"],
    )
    indexador = Indexador(
        cliente, manifiesto, cliente.picklist_matriculas(),
        al_guardar=lambda m: manifiestos.guardar(m, carpeta),
    )
    plan = indexador.planificar(3)
    assert len(plan.escribibles) == 3

    escribir_csv(plan, carpeta / "revision.csv")
    assert (carpeta / "revision.csv").is_file()
    assert cliente.escrituras == []  # el reporte no escribe

    resultado = indexador.aplicar(plan)
    assert resultado.escritas == 3 and resultado.fallidas == 0

    # la matricula y el log llegaron a la pagina correcta
    for pagina_num, valores, _estado in cliente.escrituras:
        esperado = manifiesto.registros[pagina_num - 1]
        assert valores[CAMPO_LOG_NUMBER] == esperado.log_number
        assert valores[CAMPO_MATRICULA] == esperado.matricula

    validas, total, problemas = verificar_lote(cliente, manifiesto)
    assert (validas, total, problemas) == (3, 3, [])

    guardado = manifiestos.cargar(carpeta)
    assert all(r.estado is EstadoRegistro.ESCRITA
               for r in guardado.registros)


def test_csv_de_otro_lote_no_escribe_nada(tmp_path):
    """El caso que de verdad importa: CSV y batch que no corresponden."""
    manifiesto = _preparar(tmp_path)
    manifiesto.batch_id = "003BBB"
    cliente = ClienteFalso(
        paginas={
            1: pagina(1, valores={CAMPO_LOG_NUMBER: "9990001"}),
            2: pagina(2, valores={CAMPO_LOG_NUMBER: "9990002"}),
            3: pagina(3, valores={CAMPO_LOG_NUMBER: "9990003"}),
        },
        page_count=3,
        picklist=["HP-1848CMP", "HP-9912CMP"],
    )
    indexador = Indexador(cliente, manifiesto, cliente.picklist_matriculas())
    plan = indexador.planificar(3)
    assert plan.escribibles == []
    indexador.aplicar(plan)
    assert cliente.escrituras == []


def test_flota_inferida_no_bloquea_pero_queda_marcada(tmp_path):
    manifiesto = _preparar(tmp_path)
    manifiesto.batch_id = "003BBB"
    inferidas = [r for r in manifiesto.registros if r.fleet_inferido]
    assert [r.matricula for r in inferidas] == ["HP-9912CMP"]
    assert inferidas[0].fleet == "MAX"

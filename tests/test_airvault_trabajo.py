"""Las etapas del trabajo tal como las corre la interfaz.

Cubre el recorrido que comparte la ventana con la linea de comandos: que
un trabajo a medias se retome sin repetir escrituras, que la subida no se
haga dos veces y que un CSV de otra corrida no se cuele en el lote de la
anterior. Todo contra el cliente falso; ninguna prueba toca la red.
"""

from __future__ import annotations

import pytest

from app.airvault import manifest as manifiestos
from app.airvault.config import AirVaultConfig
from app.airvault.flujo import (
    ErrorDeCorrida,
    Trabajo,
    carpeta_de_corrida,
    carpeta_de_trabajo,
    pdf_unico_de_corrida,
    pdfs_de_corrida,
)
from app.airvault.model import EstadoEtapa, EstadoRegistro
from tests.airvault_fake import ClienteFalso, lote, pagina

CSV = (
    "﻿file,page,log_number,dup,disc,matricula,flight_number,"
    "pilot_signature,captain_signature,captain_license,"
    "technician_signature,date,time_ms\n"
    "Image_001.pdf,1,2312238,false,false,HP-1848CMP,472,true,false,false,"
    "true,2026/08/12,10372.0\n"
    "Image_001.pdf,2,2312239,false,false,HP-1848CMP,389,true,true,true,"
    "false,2026/08/13,11268.3\n"
)


def corrida(tmp_path, nombre: str = "BITS 18 AUG 2026 05 42",
            pdfs: tuple[str, ...] = ("BITS 18 AUG 2026 05 42.pdf",)):
    """Arma en el temporal la carpeta que deja una corrida terminada."""
    carpeta = tmp_path / "output" / nombre
    (carpeta / "datos").mkdir(parents=True)
    csv = carpeta / "datos" / f"{nombre}.CSV"
    csv.write_text(CSV, encoding="utf-8")
    for pdf in pdfs:
        (carpeta / pdf).write_bytes(b"%PDF-1.4\n")
    return csv


def cliente_con_lote(batch_id: str = "003SRO", paginas: int = 2,
                     nombre: str = "DP | BIT 18 AUG 2026 05 42"):
    return ClienteFalso(
        paginas={n: pagina(n, estado=3) for n in range(1, paginas + 1)},
        lotes=[lote(batch_id, nombre, paginas)],
        picklist=["HP-1848CMP"],
        page_count=paginas,
    )


# ── ubicacion de los archivos de la corrida ────────────────────────

def test_la_carpeta_de_la_corrida_sale_del_csv(tmp_path):
    csv = corrida(tmp_path)
    assert carpeta_de_corrida(csv).name == "BITS 18 AUG 2026 05 42"


def test_encuentra_el_pdf_de_entrega(tmp_path):
    csv = corrida(tmp_path)
    assert pdf_unico_de_corrida(csv).name == "BITS 18 AUG 2026 05 42.pdf"


def test_sin_pdf_no_hay_nada_que_subir(tmp_path):
    csv = corrida(tmp_path, pdfs=())
    with pytest.raises(ErrorDeCorrida) as fallo:
        pdf_unico_de_corrida(csv)
    assert "exportarla" in str(fallo.value)


def test_con_varios_pdf_no_se_adivina_el_orden(tmp_path):
    """El orden del lote es el del archivo subido; con varios no se sabe."""
    csv = corrida(tmp_path, pdfs=("HP-1848CMP.pdf", "HP-1849CMP.pdf"))
    with pytest.raises(ErrorDeCorrida) as fallo:
        pdf_unico_de_corrida(csv)
    assert "un solo PDF" in str(fallo.value)
    assert len(pdfs_de_corrida(csv)) == 2


def test_el_trabajo_se_llama_como_la_corrida(tmp_path):
    csv = corrida(tmp_path)
    destino = carpeta_de_trabajo(carpeta_de_corrida(csv).name)
    assert destino.name == "BITS 18 AUG 2026 05 42"
    assert destino.parent.name == "airvault"


# ── preparacion y reanudacion ──────────────────────────────────────

def test_preparar_deja_el_manifiesto_en_disco(tmp_path):
    csv = corrida(tmp_path)
    trabajo = Trabajo.preparar(
        AirVaultConfig(), tmp_path / "job", csv, "DP | BIT 18 AUG 2026 05 42"
    )
    assert manifiestos.existe(tmp_path / "job")
    assert len(trabajo.manifiesto.registros) == 2
    assert trabajo.manifiesto.nombre_batch == "DP | BIT 18 AUG 2026 05 42"


def test_el_lote_se_llama_como_la_corrida_si_no_se_dice_otra_cosa(tmp_path):
    csv = corrida(tmp_path)
    trabajo = Trabajo.preparar(AirVaultConfig(), tmp_path / "job", csv)
    assert trabajo.manifiesto.nombre_batch == "DP | BIT 18 AUG 2026 05 42"


def test_un_csv_sin_bitacoras_no_arma_trabajo(tmp_path):
    csv = tmp_path / "vacio.CSV"
    csv.write_text("file,page,log_number,matricula,date\n", encoding="utf-8")
    with pytest.raises(ErrorDeCorrida):
        Trabajo.preparar(AirVaultConfig(), tmp_path / "job", csv)


def test_volver_a_revisar_retoma_el_mismo_trabajo(tmp_path):
    """Apretar el boton dos veces no puede perder lo ya escrito."""
    csv = corrida(tmp_path)
    primero = Trabajo.abrir_o_preparar(AirVaultConfig(), tmp_path / "job", csv)
    primero.manifiesto.registros[0].estado = EstadoRegistro.ESCRITA
    primero.guardar()

    segundo = Trabajo.abrir_o_preparar(AirVaultConfig(), tmp_path / "job", csv)
    assert segundo.manifiesto.registros[0].estado is EstadoRegistro.ESCRITA


def test_otra_corrida_en_la_misma_carpeta_rehace_el_trabajo(tmp_path):
    """Seguir el trabajo anterior escribiria una corrida en el lote de otra."""
    primera = corrida(tmp_path, "BITS 18 AUG 2026 05 42")
    trabajo = Trabajo.abrir_o_preparar(AirVaultConfig(), tmp_path / "job",
                                       primera)
    trabajo.manifiesto.registros[0].estado = EstadoRegistro.ESCRITA
    trabajo.guardar()

    segunda = corrida(tmp_path, "BITS 19 AUG 2026 06 10")
    rehecho = Trabajo.abrir_o_preparar(AirVaultConfig(), tmp_path / "job",
                                       segunda)
    assert rehecho.manifiesto.registros[0].estado is EstadoRegistro.PENDIENTE
    assert rehecho.manifiesto.nombre_batch == "DP | BIT 19 AUG 2026 06 10"


# ── subida ─────────────────────────────────────────────────────────

class SubidorFalso:
    def __init__(self):
        self.subidos = []

    def __call__(self, sesion, repo_id):
        return self

    def subir(self, ruta, valores, avisar=None):
        from app.airvault.uploader import ResultadoSubida

        self.subidos.append(ruta)
        if avisar is not None:
            avisar("Subiendo", 1, 1)
        return ResultadoSubida(str(ruta), True)


def test_el_lote_no_se_sube_dos_veces(tmp_path, monkeypatch):
    """Subirlo otra vez crearia un lote gemelo y no se sabria en cual escribir."""
    from app.airvault import uploader

    csv = corrida(tmp_path)
    falso = SubidorFalso()
    monkeypatch.setattr(uploader, "SubidorQuickUpload", falso)
    trabajo = Trabajo.preparar(AirVaultConfig(), tmp_path / "job", csv)
    pdf = pdf_unico_de_corrida(csv)

    trabajo.subir(object(), pdf)
    trabajo.subir(object(), pdf)
    assert len(falso.subidos) == 1
    assert trabajo.manifiesto.etapa_hecha("subir")


def test_la_subida_a_mano_se_puede_dar_por_hecha(tmp_path):
    csv = corrida(tmp_path)
    trabajo = Trabajo.preparar(AirVaultConfig(), tmp_path / "job", csv)
    trabajo.omitir_subida()
    assert trabajo.manifiesto.etapa("subir").estado is EstadoEtapa.OMITIDA
    assert trabajo.manifiesto.etapa_hecha("subir")


# ── busqueda, plan y escritura ─────────────────────────────────────

def test_encuentra_el_lote_y_lo_anota(tmp_path):
    csv = corrida(tmp_path)
    trabajo = Trabajo.preparar(AirVaultConfig(), tmp_path / "job", csv,
                               "DP | BIT 18 AUG 2026 05 42")
    batch_id = trabajo.descubrir(cliente_con_lote(), esperar=False)
    assert batch_id == "003SRO"
    assert trabajo.manifiesto.batch_id == "003SRO"


def test_el_plan_no_escribe_nada(tmp_path):
    """La condicion que sostiene los dos tiempos del panel."""
    csv = corrida(tmp_path)
    cliente = cliente_con_lote()
    trabajo = Trabajo.preparar(AirVaultConfig(), tmp_path / "job", csv,
                               "DP | BIT 18 AUG 2026 05 42")
    trabajo.descubrir(cliente, esperar=False)
    plan, _indexador = trabajo.planificar(cliente)

    assert cliente.escrituras == []
    assert len(plan.escribibles) == 2


def test_escribe_lo_que_el_plan_habia_anunciado(tmp_path):
    csv = corrida(tmp_path)
    cliente = cliente_con_lote()
    trabajo = Trabajo.preparar(AirVaultConfig(), tmp_path / "job", csv,
                               "DP | BIT 18 AUG 2026 05 42")
    trabajo.descubrir(cliente, esperar=False)
    plan, indexador = trabajo.planificar(cliente)
    resultado = trabajo.indexar(indexador, plan)

    assert resultado.escritas == 2
    assert [p for p, _v, _e in cliente.escrituras] == [1, 2]
    validas, total, _problemas = trabajo.verificar(cliente)
    assert (validas, total) == (2, 2)


def test_un_lote_con_otra_cantidad_de_paginas_no_se_toca(tmp_path):
    """Si la correspondencia por posicion esta rota, no se escribe nada."""
    from app.airvault.guards import ErrorDeGuarda

    csv = corrida(tmp_path)
    cliente = cliente_con_lote(paginas=3)
    trabajo = Trabajo.preparar(AirVaultConfig(), tmp_path / "job", csv,
                               "DP | BIT 18 AUG 2026 05 42")
    trabajo.fijar_lote("003SRO")
    with pytest.raises(ErrorDeGuarda):
        trabajo.planificar(cliente)
    assert cliente.escrituras == []


def test_sin_lote_no_se_planifica(tmp_path):
    csv = corrida(tmp_path)
    trabajo = Trabajo.preparar(AirVaultConfig(), tmp_path / "job", csv)
    with pytest.raises(ErrorDeCorrida):
        trabajo.planificar(cliente_con_lote())


def test_el_avance_llega_pagina_a_pagina(tmp_path):
    """Es lo que mueve la barra de la ventana mientras se escribe."""
    csv = corrida(tmp_path)
    cliente = cliente_con_lote()
    trabajo = Trabajo.preparar(AirVaultConfig(), tmp_path / "job", csv,
                               "DP | BIT 18 AUG 2026 05 42")
    trabajo.descubrir(cliente, esperar=False)
    plan, indexador = trabajo.planificar(cliente)

    avisos = []
    trabajo.indexar(indexador, plan,
                    avisar=lambda t, h, n: avisos.append((h, n)))
    assert avisos == [(1, 2), (2, 2)]

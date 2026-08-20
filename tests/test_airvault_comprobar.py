"""Esperar a AirVault y cerrar el lote cuando lo acepta.

Subir no es lo mismo que estar listo. AirVault mete el lote en su cola y
tarda —minutos, a veces mucho mas— en dejarlo indexable, asi que el
programa pregunta cada tanto en vez de quedarse esperando delante. Aqui se
fija que responde esa pregunta en cada momento, y en que condiciones el
lote se puede dar por terminado.

Nada de esto toca la red: todo va contra el cliente falso.
"""

from __future__ import annotations

import json

import pytest

from app.airvault.client import PaginaDelLote
from app.airvault.config import AirVaultConfig
from app.airvault.flujo import (
    BUSCANDO,
    COMPLETADO,
    INDEXADO,
    LISTO,
    PROCESANDO,
    SIN_SUBIR,
    SOLO_REVISAR,
    TOMADO,
    Trabajo,
    cargar_partes,
    comprobar_partes,
    completar_partes,
    estado_local,
    preparar_partes,
    ruta_indice_paginas,
    subir_partes,
)
from app.airvault.model import EstadoEtapa
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
            pdfs: tuple[str, ...] = (), con_divisoria: bool = False):
    """La carpeta que deja una ejecucion exportada.

    Con ``con_divisoria`` el PDF lleva delante la pagina que abre el grupo
    de un avion, como la entrega de verdad: ocupa sitio en el lote sin ser
    una bitacora.
    """
    carpeta = tmp_path / "output" / nombre
    (carpeta / "datos").mkdir(parents=True)
    csv = carpeta / "datos" / f"{nombre}.CSV"
    csv.write_text(CSV, encoding="utf-8")
    archivos = list(pdfs) or [f"{nombre}.pdf"]
    for pdf in archivos:
        (carpeta / pdf).write_bytes(b"%PDF-1.4\n")
    delante = [{"separador": "HP-1848CMP"}] if con_divisoria else []
    paginas = [
        delante + [{"archivo": "Image_001.pdf", "pagina": 1},
                   {"archivo": "Image_001.pdf", "pagina": 2}]
        if len(archivos) == 1
        else [{"archivo": "Image_001.pdf", "pagina": n + 1}]
        for n in range(len(archivos))
    ]
    ruta_indice_paginas(csv).write_text(
        json.dumps({"version": 2, "partes": [
            {"pdf": archivo, "paginas": suyas}
            for archivo, suyas in zip(archivos, paginas)
        ]}),
        encoding="utf-8",
    )
    return csv


def trabajo_subido(tmp_path, paginas_en_airvault: int = 2,
                   batch_id: str = "003SRO"):
    """Un trabajo de una parte, ya subida, y el cliente que la ve."""
    csv = corrida(tmp_path)
    trabajo = Trabajo.preparar(AirVaultConfig(), tmp_path / "job", csv,
                               "DP | BIT 18 AUG 2026 05 42")
    trabajo.manifiesto.etapa("subir").marcar(EstadoEtapa.HECHA, "entrega.pdf")
    # Lo que la subida anota antes de mandar nada: es lo unico con lo que
    # despues se reconoce el lote propio, porque Quick Upload los llama a
    # todos «Empty-Batch».
    trabajo.manifiesto.lotes_previos = ["003VIEJO"]
    trabajo.guardar()
    cliente = ClienteFalso(
        paginas={n: pagina(n, estado=3) for n in range(1, 3)},
        lotes=[lote("003VIEJO", "DP | LO DE ANTES", 9),
               lote(batch_id, "Empty-Batch", paginas_en_airvault)],
        picklist=["HP-1848CMP"],
        page_count=2,
    )
    return trabajo, cliente


# ── en que va cada parte ───────────────────────────────────────────

def test_sin_subir_lo_dice_y_no_pregunta_por_ningun_lote(tmp_path):
    csv = corrida(tmp_path)
    trabajo = Trabajo.preparar(AirVaultConfig(), tmp_path / "job", csv,
                               "DP | BIT 18 AUG 2026 05 42")
    cliente = ClienteFalso()
    parte, = comprobar_partes([trabajo], cliente)
    assert parte.estado == SIN_SUBIR
    assert not parte.se_puede_indexar


def test_subido_pero_todavia_no_en_la_cola_no_es_un_fallo(tmp_path):
    """AirVault tarda en sacar un lote recien subido; eso es lo normal."""
    trabajo, cliente = trabajo_subido(tmp_path)
    cliente.lotes = []
    parte, = comprobar_partes([trabajo], cliente)
    assert parte.estado == BUSCANDO
    assert not parte.se_puede_indexar
    assert not parte.se_acabo


def test_un_lote_a_medio_procesar_todavia_no_esta_listo(tmp_path):
    """Aparece en la cola antes de tener todas sus paginas.

    Escribir asi correria cada dato a la bitacora de al lado, asi que
    mientras las paginas no cuadren la parte no se toca.
    """
    trabajo, cliente = trabajo_subido(tmp_path, paginas_en_airvault=1)
    parte, = comprobar_partes([trabajo], cliente)
    assert parte.estado == PROCESANDO
    assert "1 de 2 paginas" in parte.detalle
    assert not parte.se_puede_indexar


def test_con_todas_las_paginas_queda_listo_para_indexar(tmp_path):
    trabajo, cliente = trabajo_subido(tmp_path)
    parte, = comprobar_partes([trabajo], cliente)
    assert parte.estado == LISTO
    assert parte.se_puede_indexar
    assert parte.batch_id == "003SRO"


def test_el_lote_encontrado_queda_anotado_y_con_su_nombre(tmp_path):
    """Quick Upload no admite nombre: todos llegan como «Empty-Batch»."""
    trabajo, cliente = trabajo_subido(tmp_path)
    comprobar_partes([trabajo], cliente)
    assert trabajo.manifiesto.batch_id == "003SRO"
    assert trabajo.manifiesto.etapa_hecha("descubrir")


def test_un_lote_abierto_por_otro_no_se_ofrece_para_indexar(tmp_path):
    """AirVault no lo entrega: abrirlo deja la peticion colgada."""
    trabajo, cliente = trabajo_subido(tmp_path)
    cliente.lotes = [lote("003VIEJO", "DP | LO DE ANTES", 9),
                     lote("003SRO", "Empty-Batch", 2,
                          bloqueado_por="Diego Vargas")]
    parte, = comprobar_partes([trabajo], cliente)
    assert parte.estado == TOMADO
    assert "Diego Vargas" in parte.detalle
    assert not parte.se_puede_indexar


def test_el_lote_de_revisar_no_se_ofrece_para_indexar(tmp_path):
    """Existe para que una persona lo indexe a mano."""
    trabajo, cliente = trabajo_subido(tmp_path)
    trabajo.manifiesto.solo_subir = True
    parte, = comprobar_partes([trabajo], cliente)
    assert parte.estado == SOLO_REVISAR
    assert not parte.se_puede_indexar
    assert parte.se_acabo


def test_lo_ya_indexado_deja_de_esperar_a_nada(tmp_path):
    trabajo, cliente = trabajo_subido(tmp_path)
    trabajo.manifiesto.etapa("indexar").marcar(EstadoEtapa.HECHA, "escritas 2")
    parte, = comprobar_partes([trabajo], cliente)
    assert parte.estado == INDEXADO
    assert parte.se_acabo


def test_un_lote_ya_cerrado_no_se_vuelve_a_buscar(tmp_path):
    """Cerrado sale de la cola: buscarlo alli seria no encontrarlo nunca."""
    trabajo, cliente = trabajo_subido(tmp_path)
    trabajo.manifiesto.etapa("completar").marcar(EstadoEtapa.HECHA, "29 en verde")
    cliente.lotes = []
    parte, = comprobar_partes([trabajo], cliente)
    assert parte.estado == COMPLETADO
    assert parte.se_acabo


def test_comprobar_pide_la_cola_una_sola_vez_para_todas_las_partes(tmp_path):
    """Es lo que permite preguntar cada pocos minutos sin cargar el servidor."""
    trabajo, cliente = trabajo_subido(tmp_path)
    otro, _ = trabajo_subido(tmp_path / "otra")
    cliente.filtros.clear()
    comprobar_partes([trabajo, otro], cliente)
    assert len(cliente.filtros) == 1


def test_el_estado_local_no_le_pregunta_nada_a_airvault(tmp_path):
    """Al elegir una ejecucion la lista se pinta antes de tocar la red."""
    trabajo, _cliente = trabajo_subido(tmp_path)
    parte = estado_local(trabajo)
    assert parte.estado == BUSCANDO
    assert "falta comprobar" in parte.detalle


# ── retomar una ejecucion de ayer ──────────────────────────────────

def test_una_ejecucion_ya_preparada_se_retoma_sin_rehacerla(tmp_path):
    csv = corrida(tmp_path)
    config = AirVaultConfig()
    preparar_partes(config, tmp_path / "job", csv, "DP | BIT")
    trabajos = cargar_partes(config, tmp_path / "job", csv)
    assert len(trabajos) == 1
    assert trabajos[0].manifiesto.nombre_batch == "DP | BIT"


def test_sin_manifiesto_no_hay_nada_que_retomar(tmp_path):
    csv = corrida(tmp_path)
    assert cargar_partes(AirVaultConfig(), tmp_path / "job", csv) == []


def test_un_trabajo_de_otra_ejecucion_no_se_retoma(tmp_path):
    """Seguir con el anterior escribiria los datos de una en el lote de otra."""
    config = AirVaultConfig()
    primera = corrida(tmp_path, "BITS 18 AUG 2026 05 42")
    segunda = corrida(tmp_path, "BITS 19 AUG 2026 06 10")
    preparar_partes(config, tmp_path / "job", primera, "DP | BIT")
    assert cargar_partes(config, tmp_path / "job", segunda) == []


# ── subir sin quedarse esperando ───────────────────────────────────

class SesionFalsa:
    """Se traga la subida sin red."""


def test_entre_partes_se_espera_pero_detras_de_la_ultima_no(tmp_path,
                                                            monkeypatch):
    """AirVault junta en un mismo lote los archivos que le llegan seguidos.

    Por eso hay que esperar a que la parte anterior aparezca en la cola
    antes de mandar la siguiente. Detras de la ultima no se espera a nada:
    ahi la subida termina y que el servidor la procese se pregunta despues,
    que puede tardar mucho mas que la propia subida.
    """
    csv = corrida(tmp_path, pdfs=("a.pdf", "b.pdf"))
    config = AirVaultConfig(espera_descubrimiento_s=0, espera_maxima_s=5)
    trabajos = preparar_partes(config, tmp_path / "job", csv, "DP | BIT")
    cliente = ClienteFalso(lotes=[lote("003VIEJO", "otro", 9)])

    subidas: list[str] = []

    def subir(self, sesion, pdf="", avisar=None, cliente=None):
        # Igual que la subida de verdad: anota la cola antes de mandar
        # nada, que es lo unico con lo que despues se reconoce el lote
        # propio —Quick Upload los llama a todos «Empty-Batch»—.
        self.manifiesto.lotes_previos = [
            l.batch_id for l in cliente.listar_lotes()
        ]
        subidas.append(self.manifiesto.nombre_batch)
        # Al terminar cada subida el lote aparece en la cola, como en
        # AirVault: es lo que deja seguir con la siguiente parte.
        cliente.lotes.append(
            lote(f"00{len(subidas)}", "Empty-Batch", 1)
        )
        self.manifiesto.etapa("subir").marcar(EstadoEtapa.HECHA, "ok")

    monkeypatch.setattr(Trabajo, "subir", subir)
    subir_partes(trabajos, SesionFalsa(), cliente=cliente,
                 dormir=lambda _s: None)

    assert subidas == ["DP | BIT -1", "DP | BIT -2"]
    # Las dos quedaron en lotes distintos, que es para lo que se reparten.
    assert trabajos[0].manifiesto.batch_id != trabajos[1].manifiesto.batch_id


# ── dar el lote por terminado ──────────────────────────────────────

def mapa(*estados: int) -> list[PaginaDelLote]:
    """El lote tal como lo devuelve AirVault, una pagina por documento."""
    return [
        PaginaDelLote(pagina=n, estado=e, inicio_documento=n)
        for n, e in enumerate(estados, start=1)
    ]


def test_un_lote_entero_en_verde_se_cierra(tmp_path):
    trabajo, cliente = trabajo_subido(tmp_path)
    trabajo.fijar_lote("003SRO")
    cliente.mapa = mapa(0, 0)
    resultado = trabajo.completar(cliente)
    assert resultado.completado
    assert cliente.completados == ["003SRO"]
    assert trabajo.manifiesto.etapa_hecha("completar")


def test_una_pagina_fuera_de_verde_impide_cerrarlo_y_se_dice_cual(tmp_path):
    """Casi siempre es la fecha, que AirVault deja en «Need Correction».

    No se intenta cerrarlo: AirVault lo rechazaria igual, y el lote tiene
    que quedarse en la cola para que alguien arregle esa pagina.
    """
    trabajo, cliente = trabajo_subido(tmp_path)
    trabajo.fijar_lote("003SRO")
    cliente.mapa = mapa(0, 3)
    resultado = trabajo.completar(cliente)
    assert not resultado.completado
    assert resultado.bloqueadas == [2]
    assert "2" in resultado.detalle
    assert cliente.completados == []


def test_una_pagina_sin_plantilla_tambien_impide_cerrar(tmp_path):
    """«No Template Match» tampoco es verde, aunque no sea amarillo.

    Medido en el lote 003SUS: sus trece paginas separadoras quedaron en
    estado 1 y AirVault las contaba igual que a una bitacora incompleta.
    """
    trabajo, cliente = trabajo_subido(tmp_path)
    trabajo.fijar_lote("003SRO")
    cliente.mapa = mapa(1, 0)
    resultado = trabajo.completar(cliente)
    assert not resultado.completado
    assert resultado.bloqueadas == [1]


def trabajo_con_divisoria(tmp_path, batch_id: str = "003SRO"):
    """Una parte ya subida cuyo PDF lleva una divisoria delante."""
    csv = corrida(tmp_path, con_divisoria=True)
    trabajo = Trabajo.preparar(AirVaultConfig(), tmp_path / "job", csv,
                               "DP | BIT 18 AUG 2026 05 42")
    trabajo.manifiesto.etapa("subir").marcar(EstadoEtapa.HECHA, "entrega.pdf")
    trabajo.manifiesto.lotes_previos = ["003VIEJO"]
    trabajo.guardar()
    trabajo.fijar_lote(batch_id)
    cliente = ClienteFalso(
        lotes=[lote("003VIEJO", "DP | LO DE ANTES", 9),
               lote(batch_id, "Empty-Batch", 3)],
        page_count=3,
    )
    return trabajo, cliente


def test_las_divisorias_se_quitan_del_lote_para_poder_cerrarlo(tmp_path):
    """No son documentos: no tienen fecha ni avion que escribirles.

    Quitarlas es lo mismo que hace a mano quien indexa, y es lo unico que
    deja cerrar una entrega que se subio con sus separadores.
    """
    trabajo, cliente = trabajo_con_divisoria(tmp_path)
    cliente.mapa = mapa(1, 0, 0)
    resultado = trabajo.completar(cliente)
    assert resultado.completado
    assert resultado.quitadas == [1]
    assert cliente.borradas == [1]
    assert cliente.completados == ["003SRO"]


def test_con_una_bitacora_en_amarillo_no_se_toca_ninguna_divisoria(tmp_path):
    """El lote no se va a cerrar hoy; mas vale dejarlo como estaba."""
    trabajo, cliente = trabajo_con_divisoria(tmp_path)
    cliente.mapa = mapa(1, 0, 3)
    resultado = trabajo.completar(cliente)
    assert not resultado.completado
    assert resultado.bloqueadas == [3]
    assert cliente.borradas == []
    assert cliente.completados == []


def test_sin_permiso_para_quitar_paginas_el_lote_no_se_cierra(tmp_path):
    """Quitar paginas pide «Delete Batch Image», que no toda cuenta tiene."""
    trabajo, cliente = trabajo_con_divisoria(tmp_path)
    cliente.mapa = mapa(1, 0, 0)
    cliente.no_se_pueden_borrar = {1}
    resultado = trabajo.completar(cliente)
    assert not resultado.completado
    assert resultado.bloqueadas == [1]
    assert "Delete Batch Image" in resultado.detalle
    assert cliente.completados == []
    # Y el lote queda suelto: nadie se queda con el en la mano.
    assert cliente.cerrados == ["003SRO"]


def test_una_pagina_borrada_no_impide_cerrar_el_lote(tmp_path):
    trabajo, cliente = trabajo_subido(tmp_path)
    trabajo.fijar_lote("003SRO")
    cliente.mapa = [
        PaginaDelLote(pagina=1, estado=3, inicio_documento=1, borrada=True),
        PaginaDelLote(pagina=2, estado=0, inicio_documento=2),
    ]
    assert trabajo.completar(cliente).completado


def test_solo_cuenta_la_pagina_que_encabeza_cada_documento(tmp_path):
    """AirVault agrupa varias paginas en un documento; manda la primera."""
    trabajo, cliente = trabajo_subido(tmp_path)
    trabajo.fijar_lote("003SRO")
    cliente.mapa = [
        PaginaDelLote(pagina=1, estado=0, inicio_documento=1),
        PaginaDelLote(pagina=2, estado=3, inicio_documento=1),
    ]
    assert trabajo.completar(cliente).completado


def test_cerrar_el_lote_no_lo_deja_tomado(tmp_path):
    """Cerrado, sale de la cola: soltarlo despues seria pedir un imposible."""
    trabajo, cliente = trabajo_subido(tmp_path)
    trabajo.fijar_lote("003SRO")
    cliente.mapa = mapa(0, 0)
    trabajo.completar(cliente)
    trabajo.cerrar(cliente)
    assert cliente.cerrados == []


def test_si_airvault_rechaza_el_cierre_el_lote_no_queda_bloqueado(tmp_path):
    trabajo, cliente = trabajo_subido(tmp_path)
    trabajo.fijar_lote("003SRO")
    cliente.mapa = mapa(0, 0)

    def no_se_deja(_batch_id):
        raise RuntimeError("AirVault dijo que no")

    cliente.completar_lote = no_se_deja
    with pytest.raises(RuntimeError):
        trabajo.completar(cliente)
    assert cliente.cerrados == ["003SRO"]


def test_el_lote_de_revisar_no_se_cierra_nunca(tmp_path):
    """Se sube justo para que una persona lo mire; cerrarlo lo archivaria."""
    trabajo, cliente = trabajo_subido(tmp_path)
    trabajo.fijar_lote("003SRO")
    trabajo.manifiesto.solo_subir = True
    assert completar_partes([trabajo], cliente) == []
    assert cliente.completados == []


def test_un_lote_que_no_se_deja_cerrar_no_corta_a_los_demas(tmp_path):
    """Son lotes distintos y lo escrito en cada uno ya esta escrito."""
    primero, cliente = trabajo_subido(tmp_path)
    segundo, _ = trabajo_subido(tmp_path / "otra")
    primero.fijar_lote("003SRO")
    segundo.fijar_lote("003SRP")
    cliente.mapa = mapa(0, 0)

    llamadas: list[str] = []

    def a_veces(batch_id):
        llamadas.append(batch_id)
        if batch_id == "003SRO":
            raise RuntimeError("AirVault dijo que no")
        return {"IsError": False}

    cliente.completar_lote = a_veces
    hechos = completar_partes([primero, segundo], cliente)
    assert [r.completado for _t, r in hechos] == [False, True]
    assert llamadas == ["003SRO", "003SRP"]

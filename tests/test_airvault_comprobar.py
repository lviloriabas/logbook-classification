"""Esperar a AirVault y cerrar el batch cuando lo acepta.

Subir no es lo mismo que estar listo. AirVault mete el batch en su cola y
tarda —minutos, a veces mucho mas— en dejarlo indexable, asi que el
programa pregunta cada tanto en vez de quedarse esperando delante. Aqui se
fija que responde esa pregunta en cada momento, y en que condiciones el
batch se puede dar por terminado.

Nada de esto toca la red: todo va contra el cliente falso.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.airvault.client import PaginaDelLote
from app.airvault.config import AirVaultConfig
from app.airvault.discovery import LoteNoEncontrado
from app.airvault.flujo import (
    BUSCANDO,
    COMPLETADO,
    DESCUADRADO,
    ErrorDeCorrida,
    INCOMPLETO,
    INDEXADO,
    LISTO,
    PROCESANDO,
    SIN_SUBIR,
    SOLO_REVISAR,
    TOMADO,
    Trabajo,
    cargar_partes,
    cargar_trabajos_pendientes,
    comprobar_entrega,
    comprobar_partes,
    completar_partes,
    estado_local,
    preparar_partes,
    reiniciar_trabajos_incompletos,
    ruta_indice_paginas,
    subir_partes,
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
            pdfs: tuple[str, ...] = (), con_divisoria: bool = False):
    """La carpeta que deja una ejecucion exportada.

    Con ``con_divisoria`` el PDF lleva delante la pagina que abre el grupo
    de un avion, como la entrega de verdad: ocupa sitio en el batch sin ser
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
    trabajo.manifiesto.lotes_previos = ["003VIEJO"]
    trabajo.guardar()
    cliente = ClienteFalso(
        paginas={n: pagina(n, estado=3) for n in range(1, 3)},
        lotes=[lote("003VIEJO", "DP | LO DE ANTES", 9),
               lote(batch_id, trabajo.manifiesto.nombre_batch,
                    paginas_en_airvault)],
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
    """AirVault tarda en sacar un batch recien subido; eso es lo normal."""
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


def test_un_batch_mayor_que_el_manifiesto_se_detiene(tmp_path):
    """Un 200 para una parte de 100 no va a reducirse mientras se espera."""
    trabajo, cliente = trabajo_subido(tmp_path)
    cliente.lotes = [lote("003JUNTO", "DP | BIT", 200)]

    parte, = comprobar_partes([trabajo], cliente)

    assert parte.estado == DESCUADRADO
    assert parte.se_acabo
    assert not parte.se_puede_indexar
    assert "AirVault junto dos cargas" in parte.detalle


def test_el_lote_encontrado_queda_anotado_y_con_su_nombre(tmp_path):
    trabajo, cliente = trabajo_subido(tmp_path)
    comprobar_partes([trabajo], cliente)
    assert trabajo.manifiesto.batch_id == "003SRO"
    assert trabajo.manifiesto.etapa_hecha("descubrir")


def test_la_comprobacion_periodica_recupera_un_empty_batch_interrumpido(
    tmp_path,
):
    """Cerrar la UI tras Quick Upload no deja el batch perdido ni lo repite."""
    trabajo, cliente = trabajo_subido(tmp_path)
    cliente.lotes = [
        lote("003VIEJO", "DP | LO DE ANTES", 9),
        lote("003NUEVO", "Empty-Batch", 2),
    ]

    parte, = comprobar_partes([trabajo], cliente)

    assert parte.estado == LISTO
    assert parte.batch_id == "003NUEVO"
    assert trabajo.manifiesto.batch_id == "003NUEVO"
    assert trabajo.manifiesto.etapa_hecha("subir")
    assert trabajo.manifiesto.etapa_hecha("descubrir")


def test_el_nombre_corregido_a_mano_reemplaza_un_id_de_index_batch(tmp_path):
    trabajo, _cliente = trabajo_subido(tmp_path, batch_id="003MAL")
    trabajo.fijar_lote("003MAL")
    cliente = ClienteFalso(lotes=[
        lote("003MAL", "Index Batch", 2),
        lote("003BIEN", trabajo.manifiesto.nombre_batch, 2),
    ])

    parte, = comprobar_partes([trabajo], cliente)

    assert parte.estado == LISTO
    assert trabajo.manifiesto.batch_id == "003BIEN"


def test_un_id_cuyo_titulo_no_coincide_se_elimina(tmp_path):
    trabajo, _cliente = trabajo_subido(tmp_path, batch_id="003MAL")
    trabajo.fijar_lote("003MAL")
    cliente = ClienteFalso(lotes=[lote("003MAL", "Index Batch", 2)])

    parte, = comprobar_partes([trabajo], cliente)

    assert parte.estado == BUSCANDO
    assert parte.batch_id == ""
    assert trabajo.manifiesto.batch_id is None
    assert not trabajo.manifiesto.etapa_hecha("descubrir")


def _trabajos_principal_division_y_revisar(tmp_path):
    """Tres nombres esperados, incluidos los dos que no terminan en numero."""
    csv = corrida(tmp_path)
    nombres = ("DP | BIT", "DP | BIT -2", "DP | BIT REVISAR")
    trabajos = []
    for indice, nombre in enumerate(nombres, start=1):
        trabajo = Trabajo.preparar(
            AirVaultConfig(), tmp_path / f"job-{indice}", csv, nombre
        )
        trabajo.manifiesto.solo_subir = nombre.endswith("REVISAR")
        trabajo.manifiesto.lotes_previos = ["003VIEJO"]
        trabajo.guardar()
        trabajos.append(trabajo)
    return trabajos


def test_comprobar_busca_principal_divisiones_y_revisar(tmp_path):
    trabajos = _trabajos_principal_division_y_revisar(tmp_path)
    cliente = ClienteFalso(lotes=[
        lote("003PRI", "DP | BIT", 2),
        lote("003DOS", "DP | BIT -2", 2),
        lote("003REV", "DP | BIT REVISAR", 2),
    ])

    estados = comprobar_partes(trabajos, cliente)

    assert [t.manifiesto.batch_id for t in trabajos] == [
        "003PRI", "003DOS", "003REV"
    ]
    assert [parte.estado for parte in estados] == [
        LISTO, LISTO, SOLO_REVISAR
    ]
    assert all(t.manifiesto.etapa_hecha("subir") for t in trabajos)


def test_revisar_no_se_asigna_a_las_divisiones_sin_titulo(tmp_path):
    trabajos = _trabajos_principal_division_y_revisar(tmp_path)
    for trabajo in trabajos:
        trabajo.manifiesto.etapa("subir").marcar(EstadoEtapa.HECHA, "subido")
        trabajo.manifiesto.batch_id = "003REV"
        trabajo.manifiesto.etapa("descubrir").marcar(
            EstadoEtapa.HECHA, "003REV"
        )
        trabajo.guardar()
    cliente = ClienteFalso(lotes=[
        lote("003REV", "DP | BIT REVISAR", 2),
    ])

    estados = comprobar_partes(trabajos, cliente)

    assert [t.manifiesto.batch_id for t in trabajos] == [None, None, "003REV"]
    assert [parte.estado for parte in estados] == [
        BUSCANDO, BUSCANDO, SOLO_REVISAR
    ]


def test_descubrir_tampoco_toma_revisar_para_un_automatico(tmp_path):
    automatico, _division, revisar = (
        _trabajos_principal_division_y_revisar(tmp_path)
    )
    cliente = ClienteFalso(lotes=[
        lote("003REV", revisar.manifiesto.nombre_batch, 2),
    ])

    with pytest.raises(LoteNoEncontrado):
        automatico.descubrir(cliente, esperar=False)

    assert automatico.manifiesto.batch_id is None


def test_un_lote_abierto_por_otro_no_se_ofrece_para_indexar(tmp_path):
    """AirVault no lo entrega: abrirlo deja la peticion colgada."""
    trabajo, cliente = trabajo_subido(tmp_path)
    cliente.lotes = [lote("003VIEJO", "DP | LO DE ANTES", 9),
                     lote("003SRO", trabajo.manifiesto.nombre_batch, 2,
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
    trabajo.manifiesto.etapa("verificar").marcar(EstadoEtapa.HECHA, "2/2 en Valid")
    parte, = comprobar_partes([trabajo], cliente)
    assert parte.estado == INDEXADO
    assert parte.se_acabo


def test_indexar_hecho_no_oculta_paginas_que_no_se_verificaron(tmp_path):
    trabajo, cliente = trabajo_subido(tmp_path)
    trabajo.manifiesto.etapa("indexar").marcar(EstadoEtapa.HECHA, "escritas 2")

    parte, = comprobar_partes([trabajo], cliente)

    assert parte.estado == LISTO
    assert parte.se_puede_indexar


def test_verificacion_incompleta_muestra_el_progreso_real_y_se_reintenta(
    tmp_path,
):
    trabajo, cliente = trabajo_subido(tmp_path)
    trabajo.manifiesto.etapa("verificar").marcar(
        EstadoEtapa.ERROR, "1/2 en Valid"
    )

    parte, = comprobar_partes([trabajo], cliente)

    assert parte.estado == INCOMPLETO
    assert "1/2 en Valid" in str(parte)
    assert parte.se_puede_indexar


def test_estado_local_no_disfraza_una_verificacion_incompleta(tmp_path):
    trabajo, _cliente = trabajo_subido(tmp_path)
    trabajo.manifiesto.etapa("verificar").marcar(
        EstadoEtapa.ERROR, "1/2 en Valid"
    )

    parte = estado_local(trabajo)

    assert parte.estado == INCOMPLETO
    assert "1/2 en Valid" in str(parte)


def test_un_lote_ya_cerrado_no_se_vuelve_a_buscar(tmp_path):
    """Cerrado sale de la cola: buscarlo alli seria no encontrarlo nunca."""
    trabajo, cliente = trabajo_subido(tmp_path)
    trabajo.manifiesto.etapa("completar").marcar(EstadoEtapa.HECHA, "29 en verde")
    cliente.lotes = []
    parte, = comprobar_partes([trabajo], cliente)
    assert parte.estado == COMPLETADO
    assert parte.se_acabo


def test_un_empty_batch_tardio_no_reabre_ni_resube_un_lote_completado(
    tmp_path, monkeypatch
):
    """Un duplicado que aparece tarde no se confunde con el original cerrado."""
    trabajo, cliente = trabajo_subido(tmp_path)
    trabajo.fijar_lote("003ORIGINAL")
    trabajo.manifiesto.etapa("completar").marcar(
        EstadoEtapa.HECHA, "2 paginas en verde"
    )
    trabajo.guardar()
    cliente.lotes = [lote("003TARDIO", "Empty-Batch", 2)]
    subidas = []
    monkeypatch.setattr(
        Trabajo, "subir", lambda *args, **kwargs: subidas.append(True)
    )

    subir_partes([trabajo], SesionFalsa(), cliente=cliente)

    assert subidas == []
    assert trabajo.manifiesto.batch_id == "003ORIGINAL"
    assert trabajo.manifiesto.etapa_hecha("completar")


def test_un_cierre_omitido_no_se_confunde_con_un_batch_completado(tmp_path):
    trabajo, cliente = trabajo_subido(tmp_path)
    trabajo.manifiesto.etapa("verificar").marcar(
        EstadoEtapa.HECHA, "2/2 en Valid"
    )
    trabajo.manifiesto.etapa("completar").marcar(
        EstadoEtapa.OMITIDA, "AirVault no lo acepto"
    )

    parte, = comprobar_partes([trabajo], cliente)

    assert parte.estado == INDEXADO


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


def test_revisar_subido_sigue_esperando_hasta_que_tenga_id(tmp_path):
    revisar = _trabajos_principal_division_y_revisar(tmp_path)[2]
    revisar.manifiesto.etapa("subir").marcar(
        EstadoEtapa.HECHA, "revisar.pdf"
    )
    revisar.guardar()

    parte = estado_local(revisar)

    assert parte.estado == BUSCANDO
    assert parte.batch_id == ""


# ── retomar una ejecucion de ayer ──────────────────────────────────

def test_una_ejecucion_ya_preparada_se_retoma_sin_rehacerla(tmp_path):
    csv = corrida(tmp_path)
    config = AirVaultConfig()
    preparar_partes(config, tmp_path / "job", csv, "DP | BIT")
    trabajos = cargar_partes(config, tmp_path / "job", csv)
    assert len(trabajos) == 1
    assert trabajos[0].manifiesto.nombre_batch == "DP | BIT"


def test_un_batch_antiguo_sin_numero_se_reubica_y_no_se_reparte(tmp_path):
    csv = corrida(tmp_path)
    parte_original = comprobar_entrega(csv)[0]
    trabajo = Trabajo.preparar(
        AirVaultConfig(), tmp_path / "job", csv,
        "DP | BIT", parte=parte_original, paginas_por_batch=0,
    )
    trabajo.fijar_lote("003ANT")
    trabajo.manifiesto.csv_origen = str(
        Path("C:/Users/otro/Desktop/BITS/output")
        / csv.parent.parent.name / "datos" / csv.name
    )
    trabajo.manifiesto.pdf_origen = str(
        Path("C:/Users/otro/Desktop/BITS/output")
        / csv.parent.parent.name / parte_original.pdf.name
    )
    trabajo.guardar()

    retomados = preparar_partes(
        AirVaultConfig(), tmp_path / "job", csv,
        "DP | BIT", paginas_por_batch=1,
    )

    assert len(retomados) == 1
    assert retomados[0].manifiesto.nombre_batch == "DP | BIT"
    assert retomados[0].manifiesto.batch_id == "003ANT"
    assert Path(retomados[0].manifiesto.csv_origen) == csv.resolve()
    assert (
        Path(retomados[0].manifiesto.pdf_origen)
        == parte_original.pdf.resolve()
    )


def test_sin_manifiesto_no_hay_nada_que_retomar(tmp_path):
    csv = corrida(tmp_path)
    assert cargar_partes(AirVaultConfig(), tmp_path / "job", csv) == []


def test_un_trabajo_de_otra_ejecucion_no_se_retoma(tmp_path):
    """Seguir con el anterior escribiria los datos de una en el batch de otra."""
    config = AirVaultConfig()
    primera = corrida(tmp_path, "BITS 18 AUG 2026 05 42")
    segunda = corrida(tmp_path, "BITS 19 AUG 2026 06 10")
    preparar_partes(config, tmp_path / "job", primera, "DP | BIT")
    assert cargar_partes(config, tmp_path / "job", segunda) == []


def test_al_conectar_se_recuperan_manifiestos_subidos_y_pendientes(tmp_path):
    csv = corrida(tmp_path)
    raiz = tmp_path / "output" / "airvault"
    trabajo = Trabajo.preparar(
        AirVaultConfig(), raiz / "anterior", csv, "DP | BIT"
    )
    trabajo.manifiesto.etapa("subir").marcar(EstadoEtapa.HECHA, "ok")
    trabajo.fijar_lote("003ANT")

    encontrados = cargar_trabajos_pendientes(AirVaultConfig(), raiz)

    assert [t.manifiesto.batch_id for t in encontrados] == ["003ANT"]


def test_reiniciar_indexado_incompleto_conserva_la_subida(tmp_path):
    trabajo, _cliente = trabajo_subido(tmp_path)
    trabajo.fijar_lote("003SRO")
    trabajo.manifiesto.registros[0].estado = EstadoRegistro.ESCRITA
    trabajo.manifiesto.etapa("indexar").marcar(EstadoEtapa.HECHA, "ok")
    trabajo.manifiesto.etapa("verificar").marcar(
        EstadoEtapa.ERROR, "1/2 en Valid"
    )

    reiniciados = reiniciar_trabajos_incompletos([trabajo])

    assert reiniciados[0][1] == "indexar"
    assert trabajo.manifiesto.etapa("subir").estado is EstadoEtapa.HECHA
    assert trabajo.manifiesto.batch_id == "003SRO"
    assert trabajo.manifiesto.registros[0].estado is EstadoRegistro.PENDIENTE


def test_el_worker_reintenta_una_pagina_que_airvault_deja_amarilla(tmp_path):
    from app.gui.airvault_window import TrabajoAirVaultWorker

    class AmarillaUnaVez(ClienteFalso):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.intentos: dict[int, int] = {}

        def guardar_pagina(self, batch_id, numero, valores, estado,
                           pagina_siguiente=None):
            respuesta = super().guardar_pagina(
                batch_id, numero, valores, estado, pagina_siguiente
            )
            self.intentos[numero] = self.intentos.get(numero, 0) + 1
            if numero == 1 and self.intentos[numero] == 1:
                self.paginas[numero] = pagina(
                    numero, estado=3, valores=valores
                )
            return respuesta

    trabajo, base = trabajo_subido(tmp_path)
    trabajo.fijar_lote("003SRO")
    cliente = AmarillaUnaVez(
        paginas=base.paginas, lotes=base.lotes,
        picklist=base.picklist, page_count=2,
    )
    plan = trabajo.planificar(cliente)
    estado = {
        "cliente": cliente,
        "listos": [trabajo],
        "planes": {str(trabajo.carpeta): plan},
        "completar": False,
    }
    terminado: list[dict] = []
    worker = TrabajoAirVaultWorker("indexar", estado)
    worker.indexado.connect(terminado.append)

    worker._indexar()

    assert cliente.intentos == {1: 2, 2: 1}
    assert terminado[0]["validas"] == terminado[0]["total"] == 2


# ── subir sin quedarse esperando ───────────────────────────────────

class SesionFalsa:
    """Se traga la subida sin red."""


def test_subir_confirma_todos_y_carga_solo_la_division_pendiente(
    tmp_path, monkeypatch
):
    trabajos = _trabajos_principal_division_y_revisar(tmp_path)
    for indice, trabajo in enumerate(trabajos):
        if indice != 1:
            trabajo.manifiesto.etapa("subir").marcar(
                EstadoEtapa.HECHA, "subido"
            )
        trabajo.manifiesto.batch_id = f"ID-VIEJO-{indice}"
        trabajo.guardar()
    cliente = ClienteFalso(lotes=[
        lote("003PRI", "DP | BIT", 2),
        lote("003REV", "DP | BIT REVISAR", 2),
    ])
    subidas = []

    def subir(self, sesion, pdf="", avisar=None, cliente=None):
        subidas.append(self.manifiesto.nombre_batch)
        self.manifiesto.etapa("subir").marcar(EstadoEtapa.HECHA, "subido")
        cliente.lotes.append(
            lote("003DOS", self.manifiesto.nombre_batch, 2)
        )

    monkeypatch.setattr(Trabajo, "subir", subir)

    subir_partes(trabajos, SesionFalsa(), cliente=cliente)

    assert subidas == ["DP | BIT -2"]
    assert trabajos[0].manifiesto.batch_id == "003PRI"
    assert trabajos[2].manifiesto.batch_id == "003REV"


def test_no_repite_un_batch_que_quick_upload_ya_confirmo_mientras_procesa(
    tmp_path, monkeypatch
):
    """La demora entre Quick Upload y Web Index no autoriza un duplicado."""
    trabajo = _trabajos_principal_division_y_revisar(tmp_path)[1]
    trabajo.manifiesto.etapa("subir").marcar(
        EstadoEtapa.HECHA, "division-02.pdf"
    )
    trabajo.guardar()
    subidas = []
    avisos = []
    monkeypatch.setattr(
        Trabajo, "subir", lambda *args, **kwargs: subidas.append(True)
    )

    subir_partes(
        [trabajo], SesionFalsa(), cliente=ClienteFalso(),
        avisar=lambda texto, hechas, total: avisos.append(texto),
    )

    assert subidas == []
    assert trabajo.manifiesto.etapa("subir").estado is EstadoEtapa.HECHA
    assert any("1 sigue procesándose" in texto for texto in avisos)


def test_cada_batch_se_confirma_antes_de_subir_el_siguiente(
    tmp_path, monkeypatch
):
    """La confirmacion separa cargas para que AirVault no las agrupe."""
    trabajos = _trabajos_principal_division_y_revisar(tmp_path)
    cliente = ClienteFalso()
    eventos: list[tuple[str, str]] = []

    def subir(self, sesion, pdf="", avisar=None, cliente=None):
        eventos.append(("subir", self.manifiesto.nombre_batch))
        self.manifiesto.etapa("subir").marcar(EstadoEtapa.HECHA, "ok")

    def descubrir(self, cliente, esperar=True, dormir=None, avisar=None):
        eventos.append(("confirmar", self.manifiesto.nombre_batch))
        self.manifiesto.batch_id = f"ID-{len(eventos)}"
        return self.manifiesto.batch_id

    def actualizar(trabajos_actualizados):
        eventos.append(("actualizar", str(len(trabajos_actualizados))))

    monkeypatch.setattr(Trabajo, "subir", subir)
    monkeypatch.setattr(Trabajo, "descubrir", descubrir)

    subir_partes(
        trabajos, SesionFalsa(), cliente=cliente,
        al_finalizar_subidas=actualizar,
    )

    assert eventos == [
        ("subir", "DP | BIT"),
        ("actualizar", "3"),
        ("confirmar", "DP | BIT"),
        ("subir", "DP | BIT -2"),
        ("actualizar", "3"),
        ("confirmar", "DP | BIT -2"),
        ("subir", "DP | BIT REVISAR"),
        ("actualizar", "3"),
        ("confirmar", "DP | BIT REVISAR"),
    ]


def test_varios_empty_batch_del_mismo_tamano_conservan_su_id(
    tmp_path, monkeypatch
):
    """Las instantaneas anidadas se concilian despues de subirlos todos."""
    trabajos = _trabajos_principal_division_y_revisar(tmp_path)
    cliente = ClienteFalso(lotes=[
        lote("003VIEJO", "DP | LO DE ANTES", 9),
    ])
    ids = iter(("003UNO", "003DOS", "003TRE"))

    def subir(self, sesion, pdf="", avisar=None, cliente=None):
        self.manifiesto.lotes_previos = [
            actual.batch_id for actual in cliente.listar_lotes()
        ]
        self.manifiesto.etapa("subir").marcar(EstadoEtapa.HECHA, "ok")
        cliente.lotes.append(lote(next(ids), "Empty-Batch", 2))
        self.guardar()

    monkeypatch.setattr(Trabajo, "subir", subir)

    subir_partes(
        trabajos, SesionFalsa(), cliente=cliente, dormir=lambda _s: None,
    )

    assert [trabajo.manifiesto.batch_id for trabajo in trabajos] == [
        "003UNO", "003DOS", "003TRE",
    ]
    assert all(
        trabajo.manifiesto.etapa_hecha("descubrir")
        for trabajo in trabajos
    )


def test_el_id_se_publica_antes_de_empezar_la_siguiente_carga(
    tmp_path, monkeypatch
):
    """La UI puede asignar e indexar uno mientras sigue el recorrido."""
    trabajos = _trabajos_principal_division_y_revisar(tmp_path)
    cliente = ClienteFalso()
    eventos: list[tuple[str, str]] = []

    def subir(self, sesion, pdf="", avisar=None, cliente=None):
        eventos.append(("subir", self.manifiesto.nombre_batch))
        self.manifiesto.etapa("subir").marcar(EstadoEtapa.HECHA, "ok")

    def descubrir(self, cliente, esperar=True, dormir=None, avisar=None):
        self.manifiesto.batch_id = f"ID-{self.manifiesto.parte}"
        return self.manifiesto.batch_id

    def encontrado(trabajo, todos):
        eventos.append(("id", trabajo.manifiesto.nombre_batch))

    monkeypatch.setattr(Trabajo, "subir", subir)
    monkeypatch.setattr(Trabajo, "descubrir", descubrir)

    subir_partes(
        trabajos, SesionFalsa(), cliente=cliente,
        al_encontrar=encontrado,
    )

    assert eventos == [
        ("subir", "DP | BIT"), ("id", "DP | BIT"),
        ("subir", "DP | BIT -2"), ("id", "DP | BIT -2"),
        ("subir", "DP | BIT REVISAR"),
        ("id", "DP | BIT REVISAR"),
    ]


def test_el_indexado_arranca_mientras_se_busca_el_siguiente_batch(
    tmp_path, monkeypatch
):
    """La escritura tiene un carril distinto al de subida y descubrimiento."""
    from app.airvault.flujo import EstadoParte
    from app.gui.airvault_window import TrabajoAirVaultWorker

    trabajos = _trabajos_principal_division_y_revisar(tmp_path)[:2]
    cliente = ClienteFalso()
    eventos: list[tuple[str, str]] = []
    indexando = threading.Event()

    monkeypatch.setattr(
        "app.airvault.flujo.comprobar_entrega",
        lambda _csv: [SimpleNamespace(paginas=[1, 2])],
    )
    monkeypatch.setattr(
        "app.airvault.flujo.preparar_partes",
        lambda *args, **kwargs: trabajos,
    )
    monkeypatch.setattr(
        "app.airvault.flujo.comprobar_partes",
        lambda lotes, _cliente, avisar=None: [
            EstadoParte(lotes[0], LISTO, "2 paginas")
        ],
    )

    def subir_falso(lotes, _sesion, al_encontrar=None, **_kwargs):
        primero, segundo = lotes
        primero.manifiesto.batch_id = "ID-1"
        eventos.append(("encontrar", primero.manifiesto.nombre_batch))
        al_encontrar(primero, lotes)
        assert indexando.wait(1), "el indexado no arranco en paralelo"
        segundo.manifiesto.batch_id = "ID-2"
        eventos.append(("encontrar", segundo.manifiesto.nombre_batch))
        al_encontrar(segundo, lotes)

    monkeypatch.setattr("app.airvault.flujo.subir_partes", subir_falso)
    estado = {
        "config": AirVaultConfig(), "csv": tmp_path / "corrida.csv",
        "raiz": tmp_path, "carpeta_job": tmp_path / "job",
        "nombre_lote": "DP | BIT", "paginas_por_batch": 100,
        "sesion": SesionFalsa(), "indexar_al_encontrar": True,
        "completar": False,
    }
    worker = TrabajoAirVaultWorker("subir", estado)
    monkeypatch.setattr(worker, "_conectar", lambda: cliente)
    monkeypatch.setattr(worker, "_cliente_paralelo", lambda _c: cliente)

    def indexar_falso(trabajo, _cliente, _raiz):
        eventos.append(("indexar", trabajo.manifiesto.nombre_batch))
        indexando.set()
        return {}

    monkeypatch.setattr(worker, "_indexar_batch_encontrado", indexar_falso)

    worker._subir()

    assert eventos.index(("indexar", trabajos[0].manifiesto.nombre_batch)) < (
        eventos.index(("encontrar", trabajos[1].manifiesto.nombre_batch))
    )


def test_un_automatico_con_titulo_revisar_no_se_sube(tmp_path, monkeypatch):
    trabajos = _trabajos_principal_division_y_revisar(tmp_path)
    trabajos[0].manifiesto.nombre_batch = "DP | BIT REVISAR -1"
    subidas = []
    monkeypatch.setattr(
        Trabajo, "subir", lambda *args, **kwargs: subidas.append(True)
    )

    with pytest.raises(ErrorDeCorrida, match="marcado como automatico"):
        subir_partes(trabajos, SesionFalsa(), cliente=ClienteFalso())

    assert subidas == []


# ── dar el batch por terminado ──────────────────────────────────────

def mapa(*estados: int) -> list[PaginaDelLote]:
    """El batch tal como lo devuelve AirVault, una pagina por documento."""
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
    assert cliente.validaciones_batch == [("003SRO", [1, 2])]
    assert cliente.completados == ["003SRO"]
    assert trabajo.manifiesto.etapa_hecha("completar")


def test_una_pagina_fuera_de_verde_impide_cerrarlo_y_se_dice_cual(tmp_path):
    """Casi siempre es la fecha, que AirVault deja en «Need Correction».

    No se intenta cerrarlo: AirVault lo rechazaria igual, y el batch tiene
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

    Medido en el batch 003SUS: sus trece paginas separadoras quedaron en
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
               lote(batch_id, trabajo.manifiesto.nombre_batch, 3)],
        page_count=3,
    )
    return trabajo, cliente


def test_un_batch_con_separadores_ya_borrados_vuelve_a_quedar_listo(tmp_path):
    trabajo, cliente = trabajo_con_divisoria(tmp_path)
    trabajo.manifiesto.etapa("indexar").marcar(
        EstadoEtapa.HECHA,
        "escritas 2, omitidas 0, fallidas 0, separadores borrados 1",
    )
    trabajo.guardar()
    cliente.lotes = [lote("003SRO", trabajo.manifiesto.nombre_batch, 2)]
    cliente.page_count = 2

    parte, = comprobar_partes([trabajo], cliente)
    plan, _indexador = trabajo.planificar(cliente)

    assert parte.estado == LISTO
    assert plan.batch_id == "003SRO"


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


def test_una_divisoria_verde_tambien_se_quita_antes_de_completar(tmp_path):
    trabajo, cliente = trabajo_con_divisoria(tmp_path)
    cliente.mapa = mapa(0, 0, 0)

    resultado = trabajo.completar(cliente)

    assert resultado.completado
    assert resultado.quitadas == [1]
    assert cliente.borradas == [1]
    assert cliente.validaciones_batch == [("003SRO", [2, 3])]


def test_la_validacion_final_puede_devolver_una_pagina_a_amarillo(tmp_path):
    trabajo, cliente = trabajo_subido(tmp_path)
    trabajo.fijar_lote("003SRO")
    cliente.mapa = mapa(0, 0)
    cliente.estados_tras_validar = {2: 3}

    resultado = trabajo.completar(cliente)

    assert not resultado.completado
    assert resultado.bloqueadas == [2]
    assert "no se envio CompleteBatch" in resultado.detalle
    assert cliente.completados == []
    assert cliente.cerrados == ["003SRO"]
    assert (
        trabajo.manifiesto.etapa("verificar").estado
        is EstadoEtapa.ERROR
    )


def test_reiniciar_un_cierre_pendiente_no_reinicia_la_subida(tmp_path):
    """El PDF no vuelve a Quick Upload si Complete necesita otro intento."""
    trabajo, _cliente = trabajo_subido(tmp_path)
    trabajo.fijar_lote("003SRO")
    trabajo.manifiesto.etapa("verificar").marcar(
        EstadoEtapa.HECHA, "2/2 en Valid"
    )
    trabajo.manifiesto.etapa("completar").marcar(
        EstadoEtapa.OMITIDA, "validacion pendiente"
    )

    reiniciados = reiniciar_trabajos_incompletos([trabajo])

    assert reiniciados == [(trabajo, "completar")]
    assert trabajo.manifiesto.etapa("subir").estado is EstadoEtapa.HECHA
    assert trabajo.manifiesto.batch_id == "003SRO"


def test_con_una_bitacora_en_amarillo_no_se_toca_ninguna_divisoria(tmp_path):
    """El batch no se va a cerrar hoy; mas vale dejarlo como estaba."""
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
    # Y el batch queda suelto: nadie se queda con el en la mano.
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

    # La proteccion tambien vive en el trabajo, no solo en el recorrido de
    # varias partes: una llamada directa tampoco lo borra ni lo completa.
    resultado = trabajo.completar(cliente)
    assert not resultado.completado
    assert cliente.borradas == []
    assert cliente.completados == []


def test_un_lote_que_no_se_deja_cerrar_no_corta_a_los_demas(tmp_path):
    """Son batches distintos y lo escrito en cada uno ya esta escrito."""
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

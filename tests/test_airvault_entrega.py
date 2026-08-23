"""De la exportación al manifiesto, sobre un PDF de verdad.

Es la prueba que amarra las dos mitades. La exportación decide el orden de
la entrega y lo declara en el índice; el indexado lo lee y arma el
manifiesto. Si alguna de las dos cambia por su cuenta, la correspondencia
por posición se rompe en silencio y una bitácora termina indexada con los
datos de otra: aquí se comprueba sobre archivos escritos, no sobre
diccionarios de mentira.
"""

from __future__ import annotations

import csv
import io
import os
from pathlib import Path

import pymupdf as fitz
import pytest

from app.airvault.config import AirVaultConfig
from app.airvault.flujo import (
    CALIDAD_JPEG_COMPRESION,
    DPI_COMPRESION,
    ErrorDeCorrida,
    ParteDeEntrega,
    Trabajo,
    cargar_partes,
    comprobar_entrega,
    partes_para_airvault,
    preparar_partes,
)
from app.airvault.guards import verificar_cantidad
from app.airvault.model import EstadoEtapa
from app.models.schemas import FieldResult, PageResult, ValidationReport
from app.reports.organize import (
    NOMBRE_INDICE_PAGINAS,
    escribir_entrega,
    escribir_indice_paginas,
)

# Cuatro aviones, con dos bitácoras cada uno.
BITACORAS = [
    ("HP-1848CMP", "2271620", "2026/08/11"),
    ("HP-1848CMP", "2271621", "2026/08/12"),
    ("HP-1830CMP", "2293105", "2026/08/11"),
    ("HP-1830CMP", "2293107", "2026/08/13"),
    ("HP-9910CMP", "2275872", "2026/08/11"),
    ("HP-9910CMP", "2275873", "2026/08/12"),
    ("HP-9905CMP", "2312756", "2026/08/12"),
    ("HP-9905CMP", "2312759", "2026/08/14"),
]


def pdf_de_origen(destino):
    """Un PDF con una página por bitácora, del tamaño de un escaneo."""
    doc = fitz.open()
    try:
        for numero in range(1, len(BITACORAS) + 1):
            pagina = doc.new_page(width=842, height=595)
            pagina.insert_text((72, 300), f"bitacora {numero}", fontsize=24)
        doc.save(str(destino))
    finally:
        doc.close()
    return destino


def reportes(pdf):
    paginas = []
    for numero, (matricula, log, fecha) in enumerate(BITACORAS, start=1):
        pagina = PageResult(page_number=numero, date=fecha)
        for campo, valor in (("matricula", matricula), ("log_number", log)):
            pagina.add_field(FieldResult(
                page_number=numero, field_id=campo, field_type="ocr",
                value=valor, confidence=1.0, status="OK",
            ))
        paginas.append(pagina)
    return [ValidationReport(
        pdf_path=str(pdf), template_name="fixture", pages=paginas
    )]


def corrida(tmp_path, paginas_por_parte=0, separar=("avion",)):
    """Exporta una ejecución completa y devuelve la ruta de su CSV."""
    run_dir = tmp_path / "BITS 19 AUG 2026 10 00"
    datos = run_dir / "datos"
    datos.mkdir(parents=True)
    fuente = pdf_de_origen(datos / "paginas.pdf")

    partes = escribir_entrega(
        reportes(fuente), run_dir, separar_por=separar,
        paginas_por_parte=paginas_por_parte,
    )
    escribir_indice_paginas(
        partes, datos / f"{run_dir.name}{NOMBRE_INDICE_PAGINAS}"
    )

    csv_path = datos / f"{run_dir.name}.CSV"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        escritor = csv.DictWriter(handle, fieldnames=[
            "file", "page", "log_number", "dup", "disc", "matricula", "date",
        ])
        escritor.writeheader()
        for numero, (matricula, log, fecha) in enumerate(BITACORAS, start=1):
            escritor.writerow({
                "file": fuente.name, "page": numero, "log_number": log,
                "dup": "false", "disc": "false", "matricula": matricula,
                "date": fecha,
            })
    return csv_path, partes


def principales(partes):
    return [a for a in partes if not a.revisar]


def paginas_del_pdf(ruta) -> int:
    doc = fitz.open(ruta)
    try:
        return doc.page_count
    finally:
        doc.close()


# ── una sola entrega ───────────────────────────────────────────────

def test_el_manifiesto_tiene_una_entrada_por_pagina_del_pdf(tmp_path):
    """La guarda de cantidad compara justo esto contra el batch de AirVault."""
    csv_path, partes = corrida(tmp_path)
    trabajo = Trabajo.preparar(AirVaultConfig(), tmp_path / "job", csv_path)

    en_el_pdf = paginas_del_pdf(partes[0].ruta)
    assert len(trabajo.manifiesto.registros) == en_el_pdf
    verificar_cantidad(trabajo.manifiesto.registros, en_el_pdf)


def test_cuatro_aviones_dejan_cuatro_separadores(tmp_path):
    csv_path, _partes = corrida(tmp_path)
    trabajo = Trabajo.preparar(AirVaultConfig(), tmp_path / "job", csv_path)

    assert len(trabajo.manifiesto.bitacoras()) == 8
    assert len(trabajo.manifiesto.separadores()) == 4


def test_cada_bitacora_cae_donde_esta_su_pagina(tmp_path):
    """El separador de cada avion va justo antes de sus bitacoras."""
    csv_path, _partes = corrida(tmp_path)
    trabajo = Trabajo.preparar(AirVaultConfig(), tmp_path / "job", csv_path)

    secuencia = [
        r.separador or r.matricula for r in trabajo.manifiesto.registros
    ]
    # Las secciones van por matricula ascendente: 1830, 1848, 9905, 9910.
    assert secuencia == [
        "HP-1830CMP", "HP-1830CMP", "HP-1830CMP",
        "HP-1848CMP", "HP-1848CMP", "HP-1848CMP",
        "HP-9905CMP", "HP-9905CMP", "HP-9905CMP",
        "HP-9910CMP", "HP-9910CMP", "HP-9910CMP",
    ]


def test_sin_separar_no_sobra_ninguna_pagina(tmp_path):
    csv_path, partes = corrida(tmp_path, separar=())
    trabajo = Trabajo.preparar(AirVaultConfig(), tmp_path / "job", csv_path)

    assert paginas_del_pdf(partes[0].ruta) == 8
    assert len(trabajo.manifiesto.registros) == 8
    assert not trabajo.manifiesto.separadores()


# ── entrega repartida ──────────────────────────────────────────────

def test_cada_parte_declara_lo_que_lleva(tmp_path):
    csv_path, partes = corrida(tmp_path, paginas_por_parte=6)
    declaradas = comprobar_entrega(csv_path)

    assert len(declaradas) == len(partes) > 1
    for parte, archivo in zip(declaradas, partes):
        assert parte.pdf.name == archivo.ruta.name
        assert len(parte.paginas) == paginas_del_pdf(archivo.ruta)


def test_el_manifiesto_de_cada_parte_cuadra_con_su_archivo(tmp_path):
    """Es lo que deja escribir en el batch correcto: cada batch, su archivo."""
    csv_path, partes = corrida(tmp_path, paginas_por_parte=6)
    trabajos = preparar_partes(AirVaultConfig(), tmp_path / "job", csv_path)

    assert len(trabajos) == len(partes)
    for trabajo, archivo in zip(trabajos, partes):
        verificar_cantidad(
            trabajo.manifiesto.registros, paginas_del_pdf(archivo.ruta)
        )


def test_entre_todas_las_partes_estan_todas_las_bitacoras(tmp_path):
    """Repartir no puede perder ni duplicar ninguna."""
    csv_path, _partes = corrida(tmp_path, paginas_por_parte=6)
    trabajos = preparar_partes(AirVaultConfig(), tmp_path / "job", csv_path)

    logs = [
        r.log_number
        for t in trabajos for r in t.manifiesto.bitacoras()
    ]
    assert sorted(logs) == sorted(log for _m, log, _f in BITACORAS)


def test_cada_parte_lleva_su_numero_en_el_nombre_del_lote(tmp_path):
    csv_path, _partes = corrida(tmp_path, paginas_por_parte=6)
    trabajos = preparar_partes(
        AirVaultConfig(), tmp_path / "job", csv_path, "DP | BITS PRUEBA"
    )
    nombres = [t.manifiesto.nombre_batch for t in trabajos]
    assert nombres == [
        f"DP | BITS PRUEBA -{n}" for n in range(1, len(trabajos) + 1)
    ]


def test_el_reparto_no_parte_un_avion_si_cabe_entero(tmp_path):
    """Con sitio de sobra, las bitacoras de un avion no se separan."""
    csv_path, _partes = corrida(tmp_path, paginas_por_parte=6)
    trabajos = preparar_partes(AirVaultConfig(), tmp_path / "job", csv_path)

    for trabajo in trabajos:
        matriculas = {
            r.matricula for r in trabajo.manifiesto.bitacoras()
        }
        for matricula in matriculas:
            en_otras = any(
                matricula in {b.matricula for b in otro.manifiesto.bitacoras()}
                for otro in trabajos if otro is not trabajo
            )
            assert not en_otras, f"{matricula} quedo en dos batches"


def test_una_seccion_que_no_cabe_repite_su_separador(tmp_path):
    """Sin repetirlo, la parte siguiente abriria con bitacoras sueltas."""
    csv_path, partes = corrida(tmp_path, paginas_por_parte=2)
    declaradas = comprobar_entrega(csv_path)

    assert len(declaradas) > len(BITACORAS) // 2
    # Cada parte abre con un separador, sea el de su seccion o el repetido.
    for parte in declaradas:
        assert parte.paginas[0].get("separador")


# ── limite propio de Quick Upload ──────────────────────────────────

def test_airvault_reparte_una_entrega_que_supera_el_limite(tmp_path):
    csv_path, partes = corrida(tmp_path)
    original = partes[0].ruta.read_bytes()

    trabajos = preparar_partes(
        AirVaultConfig(), tmp_path / "job", csv_path,
        paginas_por_batch=5,
    )

    assert len(trabajos) == 4
    assert [len(t.manifiesto.registros) for t in trabajos] == [3, 3, 3, 3]
    assert all(t.manifiesto.paginas_por_batch == 5 for t in trabajos)
    for trabajo in trabajos:
        assert trabajo.manifiesto.registros[0].es_separador
        pdf = Path(trabajo.manifiesto.pdf_origen)
        assert pdf.parent.name == "cargas"
        assert paginas_del_pdf(pdf) == len(trabajo.manifiesto.registros)
    # El reparto de AirVault no reexporta ni reemplaza la entrega original.
    assert partes[0].ruta.read_bytes() == original


def test_airvault_repite_el_separador_si_una_seccion_supera_el_limite(
    tmp_path,
):
    csv_path, _partes = corrida(tmp_path, separar=())
    parte = comprobar_entrega(csv_path)[0]
    parte.paginas.insert(0, {"separador": "HP-1848CMP"})
    fuente = fitz.open(str(parte.pdf))
    con_separador = fitz.open()
    con_separador.new_page(width=842, height=595)
    con_separador.insert_pdf(fuente)
    fuente.close()
    temporal = parte.pdf.with_suffix(".nuevo.pdf")
    con_separador.save(str(temporal))
    con_separador.close()
    os.replace(temporal, parte.pdf)

    partidas = partes_para_airvault([parte], tmp_path / "job", 4)

    assert [len(p.paginas) for p in partidas] == [4, 4, 3]
    assert all(p.paginas[0].get("separador") for p in partidas)


def test_compresion_reduce_un_escaneo_y_lo_deja_a_200_dpi(tmp_path):
    """La calidad moderada reduce pixeles, no los PDF originales."""
    from PIL import Image

    fuente = tmp_path / "escaneo-300dpi.pdf"
    imagen = Image.effect_noise((2550, 3300), 35).convert("RGB")
    datos = io.BytesIO()
    imagen.save(datos, format="JPEG", quality=98)
    documento = fitz.open()
    pagina = documento.new_page(width=612, height=792)
    pagina.insert_image(pagina.rect, stream=datos.getvalue())
    documento.save(str(fuente))
    documento.close()
    original = fuente.read_bytes()

    parte = ParteDeEntrega(1, 1, fuente, [{}])
    comprimida = partes_para_airvault(
        [parte], tmp_path / "job", compresion=True
    )[0].pdf

    assert DPI_COMPRESION == 200
    assert CALIDAD_JPEG_COMPRESION == 88
    assert comprimida.name.endswith("-200dpi.pdf")
    assert comprimida.stat().st_size < fuente.stat().st_size
    assert fuente.read_bytes() == original
    with fitz.open(str(comprimida)) as salida:
        assert salida.page_count == 1
        pagina = salida[0]
        imagenes = pagina.get_images(full=True)
        assert len(imagenes) == 1
        incrustada = salida.extract_image(imagenes[0][0])
        assert incrustada["width"] == round(pagina.rect.width * 200 / 72)
        assert incrustada["height"] == round(pagina.rect.height * 200 / 72)
        assert incrustada["ext"] == "jpeg"


def test_la_compresion_queda_guardada_para_reanudar(tmp_path):
    csv_path, _partes = corrida(tmp_path)

    trabajos = preparar_partes(
        AirVaultConfig(), tmp_path / "job", csv_path, compresion=True
    )
    retomados = cargar_partes(AirVaultConfig(), tmp_path / "job", csv_path)

    assert all(t.manifiesto.compresion for t in trabajos)
    assert all(t.manifiesto.compresion for t in retomados)
    assert [t.manifiesto.pdf_origen for t in retomados] == [
        t.manifiesto.pdf_origen for t in trabajos
    ]


def test_antes_de_subir_valida_el_pdf_aunque_el_indice_diga_que_cabe(
    tmp_path,
):
    """Un PDF de 107 no puede viajar con un manifiesto que declara 100."""
    csv_path, _partes = corrida(tmp_path)
    trabajo = preparar_partes(
        AirVaultConfig(), tmp_path / "job", csv_path,
        paginas_por_batch=100,
    )[0]
    pdf = Path(trabajo.manifiesto.pdf_origen)
    alterado = pdf.with_name("alterado.pdf")
    documento = fitz.open(str(pdf))
    documento.new_page()
    documento.save(str(alterado))
    documento.close()
    os.replace(alterado, pdf)

    with pytest.raises(ErrorDeCorrida, match="datos quedarian corridos"):
        trabajo.subir(object())


def test_antes_de_subir_rechaza_paginas_que_quedarian_amarillas(tmp_path):
    csv_path, _partes = corrida(tmp_path)
    trabajo = preparar_partes(
        AirVaultConfig(), tmp_path / "job", csv_path
    )[0]
    trabajo.manifiesto.bitacoras()[0].log_number = ""

    with pytest.raises(ErrorDeCorrida, match="deben quedar en REVISAR"):
        trabajo.subir(object())

    etapa = trabajo.manifiesto.etapa("subir")
    assert etapa.estado is EstadoEtapa.ERROR
    assert "No se subio ningun archivo" in etapa.detalle


def test_antes_de_subir_respeta_el_limite_de_2048_mb(tmp_path, monkeypatch):
    csv_path, _partes = corrida(tmp_path)
    trabajo = preparar_partes(
        AirVaultConfig(), tmp_path / "job", csv_path
    )[0]
    monkeypatch.setattr("app.airvault.flujo.MAXIMO_QUICK_UPLOAD_BYTES", 1)

    with pytest.raises(ErrorDeCorrida, match="maximo de Quick Upload"):
        trabajo.subir(object())


def test_el_reparto_de_airvault_no_pierde_ni_duplica_bitacoras(tmp_path):
    csv_path, _partes = corrida(tmp_path)
    contenido_csv = csv_path.read_bytes()
    trabajos = preparar_partes(
        AirVaultConfig(), tmp_path / "job", csv_path,
        paginas_por_batch=5,
    )

    logs = [
        registro.log_number
        for trabajo in trabajos
        for registro in trabajo.manifiesto.bitacoras()
    ]
    assert sorted(logs) == sorted(log for _matricula, log, _fecha in BITACORAS)
    assert csv_path.read_bytes() == contenido_csv


def test_los_batches_repartidos_se_retoman_desde_sus_manifiestos(tmp_path):
    csv_path, _partes = corrida(tmp_path)
    preparados = preparar_partes(
        AirVaultConfig(), tmp_path / "job", csv_path,
        paginas_por_batch=5,
    )

    cargados = cargar_partes(AirVaultConfig(), tmp_path / "job", csv_path)

    assert [t.carpeta for t in cargados] == [t.carpeta for t in preparados]
    assert [t.manifiesto.pdf_origen for t in cargados] == [
        t.manifiesto.pdf_origen for t in preparados
    ]


def test_un_manifiesto_revisar_antiguo_se_puede_retomar(tmp_path):
    csv_path, _partes = corrida_con_revisar(tmp_path)
    trabajos = preparar_partes(
        AirVaultConfig(), tmp_path / "job", csv_path,
        paginas_por_batch=300,
    )
    revisar = next(t for t in trabajos if t.manifiesto.solo_subir)
    # Formato anterior: REVISAR heredaba la cuenta de los automaticos.
    revisar.manifiesto.parte = 2
    revisar.manifiesto.partes = 2
    revisar.manifiesto.paginas_por_batch = 0
    revisar.guardar()

    cargados = cargar_partes(AirVaultConfig(), tmp_path / "job", csv_path)

    assert len([t for t in cargados if t.manifiesto.solo_subir]) == 1


def test_revisar_tambien_se_reparte_si_excede_quick_upload(tmp_path):
    csv_path, _partes = corrida_con_revisar(tmp_path)
    trabajos = preparar_partes(
        AirVaultConfig(), tmp_path / "job", csv_path,
        "DP | BITS PRUEBA", paginas_por_batch=2,
    )

    revisar = [t for t in trabajos if t.manifiesto.solo_subir]
    assert len(revisar) == 2
    assert [t.manifiesto.nombre_batch for t in revisar] == [
        "DP | BITS PRUEBA REVISAR -1",
        "DP | BITS PRUEBA REVISAR -2",
    ]
    assert all(len(t.manifiesto.registros) <= 2 for t in trabajos)


def test_una_corrida_sin_exportar_no_se_indexa(tmp_path):
    from app.airvault.flujo import ErrorDeCorrida

    run_dir = tmp_path / "BITS 19 AUG 2026 10 00"
    (run_dir / "datos").mkdir(parents=True)
    csv_path = run_dir / "datos" / f"{run_dir.name}.CSV"
    csv_path.write_text("file,page,log_number,matricula,date\n",
                        encoding="utf-8")
    with pytest.raises(ErrorDeCorrida):
        comprobar_entrega(csv_path)


# ── el batch de las que nadie pudo asignar ──────────────────────────

SIN_AVION = [
    ("HP-1848CMP", "2271620", "2026/08/11"),
    ("", "2271621", "2026/08/12"),
    ("HP-1830CMP", "2293105", "2026/08/11"),
    ("", "2293107", "2026/08/13"),
]


def corrida_con_revisar(tmp_path):
    """Ejecución donde dos bitacoras se quedaron sin matricula confirmada."""
    global BITACORAS
    originales = BITACORAS
    BITACORAS = SIN_AVION
    try:
        return corrida(tmp_path)
    finally:
        BITACORAS = originales


def test_las_que_no_tienen_avion_salen_en_su_propio_archivo(tmp_path):
    """En AirVault cada archivo es un batch: asi quedan en uno aparte."""
    _csv_path, partes = corrida_con_revisar(tmp_path)

    assert len(principales(partes)) == 1
    aparte = [a for a in partes if a.revisar]
    assert len(aparte) == 1
    assert aparte[0].ruta.name.endswith("REVISAR.pdf")


def test_el_archivo_principal_no_las_lleva(tmp_path):
    """Sueltas dentro del batch grande quedaban bloqueadas donde nadie mira."""
    _csv_path, partes = corrida_con_revisar(tmp_path)
    principal = principales(partes)[0]
    logs = {
        e.ref.page.fields[1].value
        for e in principal.paginas if e.ref is not None
    }
    assert logs == {"2271620", "2293105"}


def test_el_lote_de_revisar_se_llama_como_la_corrida_y_va_marcado(tmp_path):
    csv_path, _partes = corrida_con_revisar(tmp_path)
    trabajos = preparar_partes(
        AirVaultConfig(), tmp_path / "job", csv_path, "DP | BITS PRUEBA"
    )
    revisar = [t for t in trabajos if t.manifiesto.solo_subir]
    assert len(revisar) == 1
    assert revisar[0].manifiesto.nombre_batch == "DP | BITS PRUEBA REVISAR"


def test_el_lote_de_revisar_no_se_numera_como_una_parte_mas(tmp_path):
    """No es «una de dos»: es el que queda aparte."""
    csv_path, _partes = corrida_con_revisar(tmp_path)
    trabajos = preparar_partes(
        AirVaultConfig(), tmp_path / "job", csv_path, "DP | BITS PRUEBA"
    )
    principal = [t for t in trabajos if not t.manifiesto.solo_subir]
    assert len(principal) == 1
    assert principal[0].manifiesto.nombre_batch == "DP | BITS PRUEBA"


def test_en_el_lote_de_revisar_no_se_escribe_nada(tmp_path):
    from app.airvault.indexer import Indexador
    from tests.airvault_fake import ClienteFalso, pagina

    csv_path, _partes = corrida_con_revisar(tmp_path)
    trabajos = preparar_partes(AirVaultConfig(), tmp_path / "job", csv_path)
    revisar = next(t for t in trabajos if t.manifiesto.solo_subir)
    revisar.fijar_lote("003SRO")

    total = len(revisar.manifiesto.registros)
    cliente = ClienteFalso(
        paginas={n: pagina(n) for n in range(1, total + 1)},
        page_count=total,
    )
    indexador = Indexador(cliente, revisar.manifiesto, ["HP-1848CMP"])
    plan = indexador.planificar(total)
    indexador.aplicar(plan)

    assert cliente.escrituras == []
    # Tampoco se leen: serian peticiones de mas contra el servidor.
    assert cliente.lecturas == []
    assert plan.escribibles == []


def test_el_reporte_dice_que_hay_que_indexarlas_a_mano(tmp_path):
    from app.airvault.indexer import Indexador
    from tests.airvault_fake import ClienteFalso

    csv_path, _partes = corrida_con_revisar(tmp_path)
    trabajos = preparar_partes(AirVaultConfig(), tmp_path / "job", csv_path)
    revisar = next(t for t in trabajos if t.manifiesto.solo_subir)
    revisar.fijar_lote("003SRO")

    total = len(revisar.manifiesto.registros)
    plan = Indexador(ClienteFalso(page_count=total), revisar.manifiesto,
                     []).planificar(total)
    motivos = {a.codigo for p in plan.bloqueadas for a in p.avisos}
    assert motivos == {"revisar_a_mano"}


def test_sin_bitacoras_sueltas_no_hay_lote_de_revisar(tmp_path):
    _csv_path, partes = corrida(tmp_path)
    assert not [a for a in partes if a.revisar]

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

import app.airvault.flujo as flujo
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
from app.airvault.mapping import leer_csv_corrida
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

    # Todos del tamaño pedido; solo el ultimo se queda con el resto. Antes se
    # cortaba entre aviones y con el mismo limite salian cuatro batches de 3.
    assert len(trabajos) == 3
    assert [len(t.manifiesto.registros) for t in trabajos] == [5, 5, 3]
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


def test_la_compresion_se_hace_antes_de_repartir_los_batches(
    tmp_path, monkeypatch
):
    csv_path, _partes = corrida(tmp_path)
    llamadas = []
    real = flujo._pdf_de_carga

    def registrar(*args, **kwargs):
        resultado = real(*args, **kwargs)
        llamadas.append(
            (
                kwargs.get("comprimir", False),
                kwargs.get("fuente_pdf"),
                list(args[2]),
                resultado,
            )
        )
        return resultado

    monkeypatch.setattr(flujo, "_pdf_de_carga", registrar)
    trabajos = preparar_partes(
        AirVaultConfig(), tmp_path / "job", csv_path,
        paginas_por_batch=5, compresion=True,
    )

    assert len(trabajos) == 3
    assert [llamada[0] for llamada in llamadas] == [True, False, False, False]
    comprimido = llamadas[0][3]
    assert llamadas[0][2] == list(range(12))
    assert all(llamada[1] == comprimido for llamada in llamadas[1:])


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


def test_antes_de_subir_rechaza_paginas_amarillas_en_batch_automatico(
    tmp_path, monkeypatch
):
    from app.airvault.uploader import ResultadoSubida

    class SubidorQueAcepta:
        def __init__(self, *_args):
            pass

        def subir(self, ruta, _valores, avisar=None):
            return ResultadoSubida(str(ruta), True)

    monkeypatch.setattr(
        "app.airvault.uploader.SubidorQuickUpload", SubidorQueAcepta
    )
    csv_path, _partes = corrida(tmp_path)
    trabajo = preparar_partes(
        AirVaultConfig(), tmp_path / "job", csv_path
    )[0]
    trabajo.manifiesto.bitacoras()[0].log_number = ""

    from app.airvault.flujo import PaginasAmarillas

    with pytest.raises(PaginasAmarillas, match="páginas amarillas") as fallo:
        trabajo.subir(object())

    assert not trabajo.manifiesto.etapa_hecha("subir")
    # El error lleva las paginas, no solo un texto: quien pregunta las
    # enseña sin volver a calcularlas.
    assert fallo.value.paginas == ["página 2: Log Page Number"]
    # Y dice como autorizarlo, que es lo que hay que hacer si el archivo
    # ya esta hecho y rehacerlo cuesta mas que indexarlas a mano.
    assert "Subir a AirVault ahora" in str(fallo.value)


def test_autorizadas_las_amarillas_el_batch_se_sube(tmp_path, monkeypatch):
    """Rehacer la exportación cuesta más que indexar esas páginas a mano.

    Evitarlas sigue siendo lo correcto (para eso está el batch REVISAR),
    pero con el archivo ya hecho la decisión es de quien sube.
    """
    from app.airvault.flujo import autorizar_amarillas
    from app.airvault.uploader import ResultadoSubida

    subidas = []

    class SubidorQueAcepta:
        def __init__(self, *_args):
            pass

        def subir(self, ruta, _valores, avisar=None):
            subidas.append(str(ruta))
            return ResultadoSubida(str(ruta), True)

    monkeypatch.setattr(
        "app.airvault.uploader.SubidorQuickUpload", SubidorQueAcepta
    )
    csv_path, _partes = corrida(tmp_path)
    trabajo = preparar_partes(
        AirVaultConfig(), tmp_path / "job", csv_path
    )[0]
    trabajo.manifiesto.bitacoras()[0].log_number = ""

    autorizar_amarillas(trabajo)
    trabajo.subir(object())

    assert subidas
    assert trabajo.manifiesto.etapa_hecha("subir")


def test_el_batch_revisar_nunca_avisa_de_amarillas(tmp_path):
    """REVISAR existe para recoger lo dudoso: se sabe que se indexa a mano."""
    from app.airvault.flujo import paginas_amarillas

    csv_path, _partes = corrida(tmp_path)
    trabajo = preparar_partes(
        AirVaultConfig(), tmp_path / "job", csv_path
    )[0]
    trabajo.manifiesto.bitacoras()[0].log_number = ""
    trabajo.manifiesto.solo_subir = True

    assert paginas_amarillas(trabajo) == []


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


def test_en_el_lote_de_revisar_no_se_manda_lo_que_airvault_no_acepta(tmp_path):
    """Sin avion no hay pagina que guardar, ni siquiera amarilla.

    Se intento mandar lo disponible confiando en que AirVault la dejara en
    «Need Correction». No lo hace: contesta 500 «Field Aircraft value is
    required» y ese rechazo paraba el batch entero. Las bitacoras de
    REVISAR sin avion se quedan para que una persona las resuelva.
    """
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
    resultado = indexador.aplicar(plan)

    assert cliente.escrituras == []
    assert resultado.omitidas == 2
    assert not resultado.interrumpido
    assert cliente.lecturas == [2, 3]
    motivos = {a.codigo for p in plan.bloqueadas for a in p.avisos}
    assert "obligatorio_vacio" in motivos
    assert plan.escribibles == []


def test_el_reporte_dice_por_que_esa_pagina_no_se_puede_escribir(tmp_path):
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
    # El reporte tiene que nombrar las dos cosas: que no se leyo el avion y
    # que por eso la pagina no se puede guardar.
    assert "matricula_vacia" in motivos
    assert "obligatorio_vacio" in motivos


def test_una_entrega_que_no_cuadra_con_su_csv_no_se_prepara():
    """Mejor pararlo aqui que subir cuatrocientas paginas vacias.

    Si el indice y el CSV no usan el mismo nombre de archivo, el manifiesto
    sale con un registro vacio por pagina. Eso no se notaba hasta tener el
    batch en AirVault, entero en amarillo y sin nada que indexar.
    """
    from app.airvault.flujo import _comprobar_que_el_csv_cuadra
    from app.airvault.model import Registro

    huerfanas = [
        Registro(seq=1, avisos=["[sin_fila] la pagina 1 de x.pdf ..."]),
        Registro(seq=2, avisos=["[sin_fila] la pagina 2 de x.pdf ..."]),
    ]

    with pytest.raises(ErrorDeCorrida) as fallo:
        _comprobar_que_el_csv_cuadra(huerfanas, "datos/EJEC.CSV")

    assert "no estan hablando de los mismos archivos" in str(fallo.value)


def test_una_pagina_suelta_sin_fila_no_para_la_entrega():
    """Una sola descuadrada se anota y el resto del batch sigue."""
    from app.airvault.flujo import _comprobar_que_el_csv_cuadra
    from app.airvault.model import Registro

    registros = [
        Registro(seq=1, matricula="HP-1848CMP"),
        Registro(seq=2, avisos=["[sin_fila] la pagina 2 de x.pdf ..."]),
    ]

    _comprobar_que_el_csv_cuadra(registros, "datos/EJEC.CSV")


def test_sin_bitacoras_sueltas_no_hay_lote_de_revisar(tmp_path):
    _csv_path, partes = corrida(tmp_path)
    assert not [a for a in partes if a.revisar]


# ── cambiar el reparto con batches ya subidos ──────────────────────


def _subido(trabajo, batch_id: str = ""):
    """Deja el trabajo como si Quick Upload ya lo hubiera aceptado."""
    trabajo.manifiesto.etapa("subir").marcar(EstadoEtapa.HECHA, "enviado")
    if batch_id:
        trabajo.manifiesto.batch_id = batch_id
    trabajo.guardar()
    return trabajo


def _bitacoras_de(trabajos):
    """Pares (archivo, pagina) de todas las bitacoras, con repeticiones."""
    return [
        (r.archivo_origen.casefold(), r.pagina_origen)
        for t in trabajos for r in t.manifiesto.bitacoras()
    ]


def test_el_reparto_vigente_no_se_rehace(tmp_path):
    """Volver a preparar con lo mismo devuelve los mismos manifiestos."""
    csv_path, _partes = corrida(tmp_path)
    primeros = preparar_partes(
        AirVaultConfig(), tmp_path / "job", csv_path, paginas_por_batch=5,
    )
    segundos = preparar_partes(
        AirVaultConfig(), tmp_path / "job", csv_path, paginas_por_batch=5,
    )

    assert [t.carpeta for t in segundos] == [t.carpeta for t in primeros]
    assert [t.manifiesto.nombre_batch for t in segundos] == [
        t.manifiesto.nombre_batch for t in primeros
    ]


def test_cambiar_el_limite_sin_haber_subido_nada_rehace_el_reparto(tmp_path):
    """Nada llego a AirVault, asi que el reparto nuevo manda entero."""
    csv_path, _partes = corrida(tmp_path)
    preparar_partes(
        AirVaultConfig(), tmp_path / "job", csv_path, paginas_por_batch=5,
    )
    rehechos = preparar_partes(
        AirVaultConfig(), tmp_path / "job", csv_path, paginas_por_batch=4,
    )

    assert [len(t.manifiesto.registros) for t in rehechos] == [4, 4, 4, 3]
    assert all(t.manifiesto.parte == n for n, t in enumerate(rehechos, 1))
    assert sorted(_bitacoras_de(rehechos)) == sorted(
        set(_bitacoras_de(rehechos))
    )


def test_cambiar_el_limite_conserva_lo_subido_y_reparte_lo_que_falta(tmp_path):
    """Ni se resube lo que ya esta en AirVault ni se pierde lo que falta."""
    csv_path, _partes = corrida(tmp_path)
    antes = preparar_partes(
        AirVaultConfig(), tmp_path / "job", csv_path, paginas_por_batch=5,
    )
    _subido(antes[0], "003PRI")
    cubiertas_antes = set(_bitacoras_de([antes[0]]))

    despues = preparar_partes(
        AirVaultConfig(), tmp_path / "job", csv_path, paginas_por_batch=4,
    )

    # El batch que ya viajo se conserva tal cual: mismo PDF y mismo nombre.
    conservado = despues[0]
    assert conservado.carpeta == antes[0].carpeta
    assert conservado.manifiesto.batch_id == "003PRI"
    assert conservado.manifiesto.pdf_origen == antes[0].manifiesto.pdf_origen
    assert set(_bitacoras_de([conservado])) == cubiertas_antes

    # Los nuevos se reparten con el limite nuevo y no repiten nada suyo.
    nuevos = despues[1:]
    assert nuevos
    assert not set(_bitacoras_de(nuevos)) & cubiertas_antes
    assert all(len(t.manifiesto.registros) <= 4 for t in nuevos)


def test_reorganizar_no_pierde_ni_duplica_ninguna_bitacora(tmp_path):
    """Es la razon de ser del reparto incremental."""
    csv_path, _partes = corrida(tmp_path)
    antes = preparar_partes(
        AirVaultConfig(), tmp_path / "job", csv_path, paginas_por_batch=5,
    )
    _subido(antes[0], "003PRI")

    despues = preparar_partes(
        AirVaultConfig(), tmp_path / "job", csv_path, paginas_por_batch=4,
    )

    todas = _bitacoras_de(despues)
    esperadas = {
        (fila["file"].casefold(), int(fila["page"]))
        for fila in leer_csv_corrida(csv_path)
    }
    assert len(todas) == len(set(todas)), "una bitacora quedo en dos batches"
    assert set(todas) == esperadas, "falta o sobra alguna bitacora"


def test_las_partes_nuevas_no_reutilizan_un_numero_ya_subido(tmp_path):
    """Ese nombre ya existe en AirVault; repetirlo haria dos batches iguales."""
    csv_path, _partes = corrida(tmp_path)
    antes = preparar_partes(
        AirVaultConfig(), tmp_path / "job", csv_path, "DP | BITS PRUEBA",
        paginas_por_batch=5,
    )
    _subido(antes[0], "003PRI")
    _subido(antes[1], "003DOS")

    despues = preparar_partes(
        AirVaultConfig(), tmp_path / "job", csv_path, "DP | BITS PRUEBA",
        paginas_por_batch=4,
    )

    numeros = [t.manifiesto.parte for t in despues]
    assert numeros == sorted(set(numeros))
    assert numeros[:2] == [1, 2]
    assert all(n > 2 for n in numeros[2:])
    nombres = [t.manifiesto.nombre_batch for t in despues]
    assert len(set(nombres)) == len(nombres)


def test_el_manifiesto_descartado_no_reaparece_como_pendiente(tmp_path):
    """Un reparto viejo en disco se ofreceria como un batch mas que subir."""
    from app.airvault.flujo import cargar_trabajos_pendientes

    csv_path, _partes = corrida(tmp_path)
    antes = preparar_partes(
        AirVaultConfig(), tmp_path / "job", csv_path, paginas_por_batch=5,
    )
    _subido(antes[0], "003PRI")
    carpetas_viejas = {t.carpeta for t in antes[1:]}

    despues = preparar_partes(
        AirVaultConfig(), tmp_path / "job", csv_path, paginas_por_batch=4,
    )

    pendientes = cargar_trabajos_pendientes(
        AirVaultConfig(), tmp_path / "job"
    )
    vivas = {t.carpeta for t in despues}
    assert not (
        {t.carpeta for t in pendientes} - vivas
    ), "quedo un manifiesto de un reparto descartado"
    # Las carpetas que ya no forman parte del reparto conservan el archivo
    # apartado, por si hubiera que mirarlo.
    for carpeta in carpetas_viejas - vivas:
        assert list(Path(carpeta).glob("manifiesto-reemplazado-*.json"))


def test_no_se_sube_un_batch_que_repite_bitacoras_de_otro(tmp_path):
    """Ultima red antes de Quick Upload: en AirVault ya no tiene arreglo."""
    from app.airvault.flujo import ErrorDeCorrida, subir_partes

    csv_path, _partes = corrida(tmp_path)
    trabajos = preparar_partes(
        AirVaultConfig(), tmp_path / "job", csv_path, paginas_por_batch=5,
    )
    _subido(trabajos[0], "003PRI")
    # Un reparto viejo que sigue creyendo que le tocan esas mismas paginas.
    intruso = trabajos[1]
    intruso.manifiesto.registros = list(trabajos[0].manifiesto.registros)
    intruso.guardar()

    with pytest.raises(ErrorDeCorrida) as fallo:
        subir_partes([intruso], object(), en_la_ejecucion=trabajos)

    assert "repite" in str(fallo.value)
    assert "dos veces" in str(fallo.value)


def test_la_cobertura_dice_que_falta_y_que_esta_repetido(tmp_path):
    from app.airvault.flujo import revisar_cobertura

    csv_path, _partes = corrida(tmp_path)
    entrega = comprobar_entrega(csv_path)
    trabajos = preparar_partes(
        AirVaultConfig(), tmp_path / "job", csv_path, paginas_por_batch=5,
    )
    _subido(trabajos[0], "003PRI")

    cobertura = revisar_cobertura(entrega, trabajos)

    assert cobertura.cubiertas == set(_bitacoras_de([trabajos[0]]))
    assert cobertura.huecos
    assert not cobertura.repetidas
    assert not cobertura.completa
    # Contados todos, incluidos los que solo estan en disco, no falta nada.
    entera = revisar_cobertura(entrega, trabajos, solo_comprometidos=False)
    assert entera.completa


def test_reorganizar_deja_la_ejecucion_entera_a_la_vista(tmp_path):
    """El batch subido conserva su reparto, pero no su cuenta de partes."""
    csv_path, _partes = corrida(tmp_path)
    antes = preparar_partes(
        AirVaultConfig(), tmp_path / "job", csv_path, paginas_por_batch=5,
    )
    _subido(antes[0], "003PRI")

    despues = preparar_partes(
        AirVaultConfig(), tmp_path / "job", csv_path, paginas_por_batch=4,
    )
    cargados = cargar_partes(AirVaultConfig(), tmp_path / "job", csv_path)

    assert len({t.manifiesto.partes for t in despues}) == 1
    assert [t.carpeta for t in cargados] == [t.carpeta for t in despues]


def test_reorganizar_no_se_repite_en_cada_llamada(tmp_path):
    """El limite viejo de un batch subido no vuelve a disparar el reparto."""
    csv_path, _partes = corrida(tmp_path)
    antes = preparar_partes(
        AirVaultConfig(), tmp_path / "job", csv_path, paginas_por_batch=5,
    )
    _subido(antes[0], "003PRI")
    primera = preparar_partes(
        AirVaultConfig(), tmp_path / "job", csv_path, paginas_por_batch=4,
    )
    apartados = len(list((tmp_path / "job").rglob("manifiesto-reemplazado-*")))

    segunda = preparar_partes(
        AirVaultConfig(), tmp_path / "job", csv_path, paginas_por_batch=4,
    )

    assert [t.carpeta for t in segunda] == [t.carpeta for t in primera]
    assert len(
        list((tmp_path / "job").rglob("manifiesto-reemplazado-*"))
    ) == apartados


def test_si_ya_esta_todo_subido_el_limite_nuevo_no_crea_nada(tmp_path):
    """Cambiar el reparto no puede volver a mandar lo que ya viajo entero."""
    csv_path, _partes = corrida(tmp_path)
    antes = preparar_partes(AirVaultConfig(), tmp_path / "job", csv_path)
    assert len(antes) == 1
    _subido(antes[0], "003UNO")

    despues = preparar_partes(
        AirVaultConfig(), tmp_path / "job", csv_path, paginas_por_batch=4,
    )

    assert [t.carpeta for t in despues] == [antes[0].carpeta]
    assert despues[0].manifiesto.batch_id == "003UNO"
    assert len(despues[0].manifiesto.registros) == 12


def test_reorganizar_una_entrega_repartida_en_varios_archivos(tmp_path):
    """Cada archivo de entrega aporta sus propios huecos."""
    csv_path, _partes = corrida(tmp_path, paginas_por_parte=6)
    antes = preparar_partes(
        AirVaultConfig(), tmp_path / "job", csv_path, paginas_por_batch=6,
    )
    _subido(antes[0], "003PRI")

    despues = preparar_partes(
        AirVaultConfig(), tmp_path / "job", csv_path, paginas_por_batch=4,
    )

    todas = _bitacoras_de(despues)
    esperadas = {
        (fila["file"].casefold(), int(fila["page"]))
        for fila in leer_csv_corrida(csv_path)
    }
    assert len(todas) == len(set(todas))
    assert set(todas) == esperadas
    assert despues[0].manifiesto.batch_id == "003PRI"


def test_dos_ejecuciones_distintas_no_se_estorban_al_subir(tmp_path):
    """El escaner nombra igual sus archivos en cada ejecucion.

    La ventana retoma los pendientes de dias anteriores junto a los de hoy,
    asi que en la misma tabla conviven batches de entregas distintas. Sus
    bitacoras se llaman igual (``Image_001.pdf`` pagina 1 existe en todas)
    sin que eso signifique que se repitan: son documentos distintos. Al no
    mirar de que entrega venia cada una, la guarda daba por repetida la
    ejecucion entera y abortaba la subida completa, de modo que ningun
    batch llegaba nunca a Quick Upload.
    """
    from app.airvault.flujo import subir_partes

    csv_uno, _ = corrida(tmp_path / "lunes")
    csv_dos, _ = corrida(tmp_path / "martes")
    de_lunes = preparar_partes(
        AirVaultConfig(), tmp_path / "job-lunes", csv_uno, paginas_por_batch=5,
    )
    de_martes = preparar_partes(
        AirVaultConfig(), tmp_path / "job-martes", csv_dos, paginas_por_batch=5,
    )
    for trabajo in de_martes:
        # Cada entrega lleva su fecha en el nombre; el ayudante de pruebas
        # usa la misma para las dos, y dos batches no pueden llamarse igual.
        trabajo.manifiesto.nombre_batch = trabajo.manifiesto.nombre_batch.replace(
            "19 AUG", "18 AUG"
        )
        trabajo.guardar()
    # Las mismas paginas de origen en las dos entregas, como en la realidad.
    assert set(_bitacoras_de(de_lunes)) == set(_bitacoras_de(de_martes))
    juntos = de_lunes + de_martes
    enviados = []

    class _Subidor:
        def subir(self, *_a, **_k):
            raise AssertionError("no se usa: se sustituye Trabajo.subir")

    for trabajo in juntos:
        trabajo.subir = (  # type: ignore[method-assign]
            lambda *_a, _t=trabajo, **_k: enviados.append(
                _t.manifiesto.nombre_batch
            )
        )

    subir_partes(juntos, object(), en_la_ejecucion=juntos)

    assert enviados == [t.manifiesto.nombre_batch for t in juntos]


def test_la_columna_entrega_dice_en_cuantos_batches_se_parte(tmp_path):
    """El historial adelanta el reparto, no solo cuántos PDF hay."""
    from app.airvault.flujo import partes_de_corrida
    from app.gui.airvault_window import batches_de_entrega, estado_de_entrega

    csv_path, _entregas = corrida(tmp_path)
    partes = partes_de_corrida(csv_path)
    paginas = sum(len(parte.paginas) for parte in partes)
    assert paginas > 2, "el fixture no da para repartir"

    # Sin límite cabe todo en un batch por archivo de entrega.
    texto, listo = estado_de_entrega(csv_path, paginas)
    assert listo
    assert texto.endswith(f"{len(partes)} batches") or texto.endswith("1 batch")

    # Con un límite de una página por batch, cada página es su propio batch.
    assert batches_de_entrega(csv_path, paginas) == len(partes)
    troceado = batches_de_entrega(csv_path, 1)
    assert troceado is None or troceado >= paginas


def test_sin_limite_la_columna_entrega_solo_cuenta_los_archivos(tmp_path):
    from app.gui.airvault_window import estado_de_entrega

    csv_path, _entregas = corrida(tmp_path)

    texto, listo = estado_de_entrega(csv_path)

    assert listo
    assert "batch" not in texto, "sin máximo no hay reparto que anunciar"


def test_una_ejecucion_sin_exportar_no_anuncia_reparto(tmp_path):
    from app.gui.airvault_window import estado_de_entrega

    run_dir = tmp_path / "BITS 19 AUG 2026 10 00"
    (run_dir / "datos").mkdir(parents=True)
    csv_path = run_dir / "datos" / f"{run_dir.name}.CSV"
    csv_path.write_text("file,page\n", encoding="utf-8-sig")

    texto, listo = estado_de_entrega(csv_path, 200)

    assert not listo
    assert texto == "Sin exportar"

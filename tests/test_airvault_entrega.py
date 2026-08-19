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

import pymupdf as fitz
import pytest

from app.airvault.config import AirVaultConfig
from app.airvault.flujo import Trabajo, comprobar_entrega, preparar_partes
from app.airvault.guards import verificar_cantidad
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
    """Exporta una corrida completa y devuelve la ruta de su CSV."""
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


def paginas_del_pdf(ruta) -> int:
    doc = fitz.open(ruta)
    try:
        return doc.page_count
    finally:
        doc.close()


# ── una sola entrega ───────────────────────────────────────────────

def test_el_manifiesto_tiene_una_entrada_por_pagina_del_pdf(tmp_path):
    """La guarda de cantidad compara justo esto contra el lote de AirVault."""
    csv_path, partes = corrida(tmp_path)
    trabajo = Trabajo.preparar(AirVaultConfig(), tmp_path / "job", csv_path)

    en_el_pdf = paginas_del_pdf(partes[0][0])
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

    assert paginas_del_pdf(partes[0][0]) == 8
    assert len(trabajo.manifiesto.registros) == 8
    assert not trabajo.manifiesto.separadores()


# ── entrega repartida ──────────────────────────────────────────────

def test_cada_parte_declara_lo_que_lleva(tmp_path):
    csv_path, partes = corrida(tmp_path, paginas_por_parte=6)
    declaradas = comprobar_entrega(csv_path)

    assert len(declaradas) == len(partes) > 1
    for parte, (ruta, _tramo) in zip(declaradas, partes):
        assert parte.pdf.name == ruta.name
        assert len(parte.paginas) == paginas_del_pdf(ruta)


def test_el_manifiesto_de_cada_parte_cuadra_con_su_archivo(tmp_path):
    """Es lo que deja escribir en el lote correcto: cada lote, su archivo."""
    csv_path, partes = corrida(tmp_path, paginas_por_parte=6)
    trabajos = preparar_partes(AirVaultConfig(), tmp_path / "job", csv_path)

    assert len(trabajos) == len(partes)
    for trabajo, (ruta, _tramo) in zip(trabajos, partes):
        verificar_cantidad(
            trabajo.manifiesto.registros, paginas_del_pdf(ruta)
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
        AirVaultConfig(), tmp_path / "job", csv_path, "DP | BIT PRUEBA"
    )
    nombres = [t.manifiesto.nombre_batch for t in trabajos]
    assert nombres == [
        f"DP | BIT PRUEBA ({n} de {len(trabajos)})"
        for n in range(1, len(trabajos) + 1)
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
            assert not en_otras, f"{matricula} quedo en dos lotes"


def test_una_seccion_que_no_cabe_repite_su_separador(tmp_path):
    """Sin repetirlo, la parte siguiente abriria con bitacoras sueltas."""
    csv_path, partes = corrida(tmp_path, paginas_por_parte=2)
    declaradas = comprobar_entrega(csv_path)

    assert len(declaradas) > len(BITACORAS) // 2
    # Cada parte abre con un separador, sea el de su seccion o el repetido.
    for parte in declaradas:
        assert parte.paginas[0].get("separador")


def test_una_corrida_sin_exportar_no_se_indexa(tmp_path):
    from app.airvault.flujo import ErrorDeCorrida

    run_dir = tmp_path / "BITS 19 AUG 2026 10 00"
    (run_dir / "datos").mkdir(parents=True)
    csv_path = run_dir / "datos" / f"{run_dir.name}.CSV"
    csv_path.write_text("file,page,log_number,matricula,date\n",
                        encoding="utf-8")
    with pytest.raises(ErrorDeCorrida):
        comprobar_entrega(csv_path)

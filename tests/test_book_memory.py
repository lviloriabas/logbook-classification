"""La memoria de libros contrastada con una fuente de autoridad.

Lo que se comprueba aquí es la política, no de dónde salen los datos: qué
respaldo hace falta para reemplazar una entrada, qué se deja intacto y qué
no se toca nunca. Sin red y sin AirVault, que es justo lo que permite
separar :mod:`app.validation.book_memory` de :mod:`app.airvault.memoria`.
"""

from __future__ import annotations

from datetime import date
import json
from pathlib import Path

from app.validation.book_memory import (
    APRENDIDO,
    CONFIRMADO,
    CONFLICTO,
    CORREGIDO,
    INVALIDO,
    Observacion,
    auditar,
    clave_de_libro,
    libros_guardados,
    verificar,
)

FLOTA = ("HP-1376CMP", "HP-1835CMP", "HP-1522WWP")


def _memoria(tmp_path: Path, matriculas=None, fechas=None):
    """Los dos archivos de memoria escritos como los escribe el programa."""
    ruta_matriculas = tmp_path / "book_matriculas.json"
    ruta_fechas = tmp_path / "book_fechas.json"
    ruta_matriculas.write_text(
        json.dumps(matriculas or {}), encoding="utf-8"
    )
    ruta_fechas.write_text(json.dumps(fechas or {}), encoding="utf-8")
    return ruta_matriculas, ruta_fechas


def _leer(ruta: Path) -> dict:
    return json.loads(ruta.read_text(encoding="utf-8"))


def _observacion(numero, matricula="", fecha=None):
    return Observacion(
        log_number=numero,
        matricula=matricula,
        fecha=date.fromisoformat(fecha) if fecha else None,
        fuente="prueba",
    )


# ── la clave del libro ─────────────────────────────────────────────

def test_la_clave_separa_las_dos_mitades_del_mismo_numero():
    assert clave_de_libro("2315912") == "23159A"
    assert clave_de_libro("2315952") == "23159B"


def test_un_numero_que_no_tiene_siete_digitos_no_es_de_ningun_libro():
    assert clave_de_libro("231591") == ""
    assert clave_de_libro("23159 12") == ""
    assert clave_de_libro("") == ""


# ── matriculas ─────────────────────────────────────────────────────

def test_dos_bitacoras_de_airvault_reemplazan_la_matricula_guardada(tmp_path):
    # El caso que motiva todo el modulo: la memoria aprendio mal y nadie la
    # vuelve a mirar, porque el corrector escribe ese valor en las cincuenta
    # paginas del libro y la ejecución siguiente se encuentra de acuerdo
    # consigo misma.
    matriculas, fechas = _memoria(tmp_path, {"23159B": "HP-1835CMP"})

    informe = verificar(
        [
            _observacion("2315952", "HP-1376CMP"),
            _observacion("2315971", "HP-1376CMP"),
        ],
        matriculas, fechas, flota=FLOTA,
    )

    assert [c.accion for c in informe.matriculas] == [CORREGIDO]
    assert _leer(matriculas) == {"23159B": "HP-1376CMP"}


def test_una_sola_bitacora_no_reemplaza_lo_guardado(tmp_path):
    # Con una pagina sola, el error podria estar en su numero de bitacora:
    # seria una pagina de otro libro, no una memoria equivocada.
    matriculas, fechas = _memoria(tmp_path, {"23159B": "HP-1835CMP"})

    informe = verificar(
        [_observacion("2315952", "HP-1376CMP")], matriculas, fechas,
        flota=FLOTA,
    )

    assert [c.accion for c in informe.matriculas] == [CONFLICTO]
    assert _leer(matriculas) == {"23159B": "HP-1835CMP"}


def test_lo_que_coincide_se_confirma_y_no_se_reescribe(tmp_path):
    matriculas, fechas = _memoria(tmp_path, {"23159B": "HP-1376CMP"})
    antes = matriculas.read_bytes()

    informe = verificar(
        [
            _observacion("2315952", "HP-1376CMP"),
            _observacion("2315971", "HP-1376CMP"),
        ],
        matriculas, fechas, flota=FLOTA,
    )

    assert [c.accion for c in informe.matriculas] == [CONFIRMADO]
    assert not informe.hay_cambios
    assert matriculas.read_bytes() == antes


def test_un_libro_que_no_estaba_se_aprende_con_una_sola_bitacora(tmp_path):
    # Aprender no compite con nada: donde no habia entrada, una pagina que
    # AirVault ya da por buena es mejor que ninguna.
    matriculas, fechas = _memoria(tmp_path)

    informe = verificar(
        [_observacion("2315952", "HP-1376CMP")], matriculas, fechas,
        flota=FLOTA,
    )

    assert [c.accion for c in informe.matriculas] == [APRENDIDO]
    assert _leer(matriculas) == {"23159B": "HP-1376CMP"}


def test_si_airvault_no_dice_lo_mismo_en_todo_el_libro_no_se_toca(tmp_path):
    matriculas, fechas = _memoria(tmp_path, {"23159B": "HP-1835CMP"})

    informe = verificar(
        [
            _observacion("2315952", "HP-1376CMP"),
            _observacion("2315971", "HP-1835CMP"),
        ],
        matriculas, fechas, flota=FLOTA,
    )

    assert [c.accion for c in informe.matriculas] == [CONFLICTO]
    assert _leer(matriculas) == {"23159B": "HP-1835CMP"}


def test_la_misma_bitacora_dos_veces_no_cuenta_como_dos_paginas(tmp_path):
    # Dos batches pueden traer la misma bitacora. Contarla dos veces le
    # daria a una sola pagina el respaldo que se exige de dos.
    matriculas, fechas = _memoria(tmp_path, {"23159B": "HP-1835CMP"})

    verificar(
        [
            _observacion("2315952", "HP-1376CMP"),
            _observacion("2315952", "HP-1376CMP"),
        ],
        matriculas, fechas, flota=FLOTA,
    )

    assert _leer(matriculas) == {"23159B": "HP-1835CMP"}


def test_se_conserva_el_sufijo_con_que_la_flota_escribe_ese_avion(tmp_path):
    # AirVault tiene el 1522 en su picklist como CMP y aquí es WWP. Lo que
    # se guarda acaba en el CSV y en los nombres de archivo, así que la
    # memoria conserva la forma local.
    matriculas, fechas = _memoria(tmp_path)

    verificar(
        [
            _observacion("2315952", "HP-1522CMP"),
            _observacion("2315971", "HP-1522CMP"),
        ],
        matriculas, fechas, flota=FLOTA,
    )

    assert _leer(matriculas) == {"23159B": "HP-1522WWP"}


def test_una_matricula_que_no_es_de_ningun_avion_se_borra(tmp_path):
    # No hace falta preguntarle a AirVault: ese avión no existe, así que la
    # entrada salió de un dígito mal leído y el corrector se la está
    # poniendo a un libro entero.
    matriculas, fechas = _memoria(tmp_path, {"23159B": "HP-9999CMP"})

    informe = verificar([], matriculas, fechas, flota=FLOTA)

    assert [c.accion for c in informe.matriculas] == [INVALIDO]
    assert _leer(matriculas) == {}


def test_sin_lista_de_flota_no_se_borra_nada(tmp_path):
    matriculas, fechas = _memoria(tmp_path, {"23159B": "HP-9999CMP"})

    informe = verificar([], matriculas, fechas)

    assert informe.matriculas == []
    assert _leer(matriculas) == {"23159B": "HP-9999CMP"}


def test_lo_que_airvault_respalda_no_se_borra_por_no_estar_en_la_flota(
    tmp_path,
):
    # La lista local se escribe a mano y envejece. Borrar lo que AirVault
    # confirma solo conseguiria aprenderlo y volver a borrarlo en cada
    # ejecución.
    matriculas, fechas = _memoria(tmp_path, {"23159B": "HP-9907CMP"})

    informe = verificar(
        [
            _observacion("2315952", "HP-9907CMP"),
            _observacion("2315971", "HP-9907CMP"),
        ],
        matriculas, fechas, flota=FLOTA,
    )

    assert [c.accion for c in informe.matriculas] == [CONFIRMADO]
    assert _leer(matriculas) == {"23159B": "HP-9907CMP"}


# ── fechas ─────────────────────────────────────────────────────────

def test_las_fechas_de_airvault_amplian_el_registro(tmp_path):
    matriculas, fechas = _memoria(
        tmp_path, fechas={"23159B": {"52": "2025-05-14"}}
    )

    verificar(
        [_observacion("2315997", fecha="2025-06-02")], matriculas, fechas,
    )

    assert _leer(fechas) == {"23159B": {"52": "2025-05-14",
                                       "97": "2025-06-02"}}


def test_dos_fechas_de_airvault_rehacen_un_registro_que_las_contradice(
    tmp_path,
):
    matriculas, fechas = _memoria(
        tmp_path, fechas={"23159B": {"52": "2024-01-01", "97": "2024-02-01"}}
    )

    informe = verificar(
        [
            _observacion("2315952", fecha="2025-05-14"),
            _observacion("2315997", fecha="2025-06-02"),
        ],
        matriculas, fechas,
    )

    assert [c.accion for c in informe.fechas] == [CORREGIDO]
    assert _leer(fechas) == {"23159B": {"52": "2025-05-14",
                                        "97": "2025-06-02"}}


def test_una_sola_fecha_en_contra_deja_el_registro_como_estaba(tmp_path):
    matriculas, fechas = _memoria(
        tmp_path, fechas={"23159B": {"52": "2024-01-01", "97": "2024-02-01"}}
    )

    informe = verificar(
        [_observacion("2315952", fecha="2025-05-14")], matriculas, fechas,
    )

    assert [c.accion for c in informe.fechas] == [CONFLICTO]
    assert _leer(fechas) == {"23159B": {"52": "2024-01-01",
                                        "97": "2024-02-01"}}


def test_si_las_fechas_de_airvault_retroceden_no_se_aprende_nada(tmp_path):
    # Dentro de un libro la fecha no retrocede al aumentar el log_number.
    # Si en AirVault lo hace, el error esta alli y no en la memoria.
    matriculas, fechas = _memoria(tmp_path)

    informe = verificar(
        [
            _observacion("2315952", fecha="2025-06-02"),
            _observacion("2315997", fecha="2025-05-14"),
        ],
        matriculas, fechas,
    )

    assert [c.accion for c in informe.fechas] == [CONFLICTO]
    assert _leer(fechas) == {}


def test_la_misma_pagina_con_dos_fechas_distintas_no_se_aprende(tmp_path):
    matriculas, fechas = _memoria(tmp_path)

    informe = verificar(
        [
            Observacion("2315952", fecha=date(2025, 5, 14)),
            Observacion("2315952", fecha=date(2025, 6, 2)),
        ],
        matriculas, fechas,
    )

    # La segunda observación de la misma bitácora se descarta al agrupar,
    # así que el libro queda con un ancla sola y no con un conflicto.
    assert _leer(fechas) == {"23159B": {"52": "2025-05-14"}}
    assert informe.observaciones == 1


# ── el informe ─────────────────────────────────────────────────────

def test_auditar_no_escribe_nada(tmp_path):
    matriculas, fechas = _memoria(tmp_path, {"23159B": "HP-1835CMP"})
    antes = matriculas.read_bytes()

    informe = auditar(
        [
            _observacion("2315952", "HP-1376CMP"),
            _observacion("2315971", "HP-1376CMP"),
        ],
        matriculas, fechas, flota=FLOTA,
    )

    assert informe.hay_cambios
    assert matriculas.read_bytes() == antes


def test_los_libros_guardados_salen_de_las_dos_memorias(tmp_path):
    matriculas, fechas = _memoria(
        tmp_path,
        {"23159B": "HP-1376CMP"},
        {"20737A": {"12": "2025-05-14"}},
    )

    assert libros_guardados(matriculas, fechas) == ["20737A", "23159B"]


def test_una_memoria_ilegible_no_detiene_la_comprobacion(tmp_path):
    matriculas = tmp_path / "book_matriculas.json"
    fechas = tmp_path / "book_fechas.json"
    matriculas.write_text("{esto no es json", encoding="utf-8")

    informe = verificar(
        [
            _observacion("2315952", "HP-1376CMP"),
            _observacion("2315971", "HP-1376CMP"),
        ],
        matriculas, fechas, flota=FLOTA,
    )

    assert [c.accion for c in informe.matriculas] == [APRENDIDO]
    assert _leer(matriculas) == {"23159B": "HP-1376CMP"}

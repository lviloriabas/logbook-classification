"""El nombre del lote es la llave para reconocerlo en AirVault."""

from __future__ import annotations

from datetime import datetime

from app.airvault.naming import (
    PREFIJO_POR_DEFECTO,
    limpiar_nombre_remoto,
    marca_de_corrida,
    marca_de_tiempo,
    nombre_de_lote,
    nombre_desde_corrida,
    prefijo_de_busqueda,
)

MOMENTO = datetime(2026, 8, 18, 5, 42)


def test_marca_igual_a_la_del_csv_de_corrida():
    assert marca_de_tiempo(MOMENTO) == "18 AUG 2026 05 42"


def test_nombre_con_el_prefijo_por_defecto():
    assert nombre_de_lote(momento=MOMENTO) == "DP | BIT 18 AUG 2026 05 42"


def test_prefijo_personalizado():
    assert nombre_de_lote("DP | ECRA", MOMENTO) == "DP | ECRA 18 AUG 2026 05 42"


def test_dia_de_un_digito_va_con_cero():
    assert marca_de_tiempo(datetime(2026, 1, 3, 9, 5)) == "03 JAN 2026 09 05"


def test_marca_se_saca_del_nombre_del_csv():
    ruta = r"output\BITS 18 AUG 2026 05 42\datos\BITS 18 AUG 2026 05 42.CSV"
    assert marca_de_corrida(ruta) == "18 AUG 2026 05 42"


def test_marca_se_saca_de_la_carpeta():
    assert marca_de_corrida("output/BITS 03 JAN 2026 09 05") == (
        "03 JAN 2026 09 05"
    )


def test_ruta_sin_marca():
    assert marca_de_corrida("output/otra_cosa/datos.csv") is None


def test_nombre_desde_corrida_usa_la_marca_de_la_corrida():
    # El lote se llama igual que la corrida que lo produjo, no como la hora
    # en que alguien se acordo de subirlo.
    ruta = "output/BITS 18 AUG 2026 05 42/datos/BITS 18 AUG 2026 05 42.CSV"
    assert nombre_desde_corrida(ruta) == "DP | BIT 18 AUG 2026 05 42"


def test_nombre_desde_corrida_sin_marca_cae_a_la_hora_actual():
    assert nombre_desde_corrida("output/x.csv", momento=MOMENTO) == (
        "DP | BIT 18 AUG 2026 05 42"
    )


def test_dos_corridas_del_mismo_minuto_dan_el_mismo_nombre():
    # Es lo esperado: la carpeta de la corrida se desempata con sufijo y el
    # lote se distingue por la cantidad de paginas.
    assert nombre_de_lote(momento=MOMENTO) == nombre_de_lote(momento=MOMENTO)


def test_nombre_remoto_se_desescapa():
    assert limpiar_nombre_remoto("DP | Bit&#225;coras varias 4") == (
        "DP | Bitácoras varias 4"
    )


def test_nombre_remoto_vacio():
    assert limpiar_nombre_remoto(None) == ""


def test_prefijo_de_busqueda():
    assert prefijo_de_busqueda("DP | BIT 18 AUG 2026 05 42") == (
        "DP | BIT 18 AUG 2026 05 42"
    )
    assert prefijo_de_busqueda("  ") == PREFIJO_POR_DEFECTO

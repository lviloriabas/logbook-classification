"""El nombre del lote es la llave para reconocerlo en AirVault."""

from __future__ import annotations

from datetime import datetime

from app.airvault.naming import (
    PREFIJO_POR_DEFECTO,
    limpiar_nombre_remoto,
    marca_de_corrida,
    marca_de_tiempo,
    nombre_de_lote,
    nombre_de_parte,
    nombre_de_revisar,
    nombre_desde_corrida,
    prefijo_de_busqueda,
)

MOMENTO = datetime(2026, 8, 18, 5, 42)


def test_marca_igual_a_la_del_csv_de_corrida():
    assert marca_de_tiempo(MOMENTO) == "18 AUG 2026 05 42"


def test_nombre_con_el_prefijo_por_defecto():
    assert nombre_de_lote(momento=MOMENTO) == "DP | BITS 18 AUG 2026 05 42"


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
    assert nombre_desde_corrida(ruta) == "DP | BITS 18 AUG 2026 05 42"


def test_nombre_desde_corrida_sin_marca_usa_el_momento_dado():
    assert nombre_desde_corrida("output/x.csv", momento=MOMENTO) == (
        "DP | BITS 18 AUG 2026 05 42"
    )


def test_sin_marca_en_el_nombre_se_toma_la_hora_del_archivo(tmp_path):
    """El lote dice cuando se proceso la bitacora, no cuando se subio."""
    import os
    from datetime import datetime as _dt

    corrida = tmp_path / "sin marca"
    (corrida / "datos").mkdir(parents=True)
    csv = corrida / "datos" / "salida.CSV"
    csv.write_text("x", encoding="utf-8")
    procesada = _dt(2026, 8, 18, 5, 42).timestamp()
    os.utime(csv, (procesada, procesada))

    assert nombre_desde_corrida(csv) == "DP | BITS 18 AUG 2026 05 42"


def test_el_nombre_va_entero_en_mayusculas():
    assert nombre_de_lote("dp | bits", MOMENTO) == (
        "DP | BITS 18 AUG 2026 05 42"
    )


def test_las_partes_se_numeran_con_sufijo():
    base = "DP | BITS 18 AUG 2026 05 42"
    assert nombre_de_parte(base, 2, 5) == "DP | BITS 18 AUG 2026 05 42 -2"


def test_una_sola_parte_no_lleva_sufijo():
    base = "DP | BITS 18 AUG 2026 05 42"
    assert nombre_de_parte(base, 1, 1) == base


def test_el_lote_de_revisar_va_marcado():
    """Se sube aparte para resolverlo a mano; tiene que verse en la cola."""
    base = "DP | BITS 18 AUG 2026 05 42"
    assert nombre_de_revisar(base) == "DP | BITS 18 AUG 2026 05 42 REVISAR"


def test_el_archivo_y_el_lote_llevan_el_mismo_sufijo():
    """Si se separan, el lote deja de poder emparejarse con su archivo."""
    from app.reports.organize import nombre_de_parte as nombre_de_archivo

    for indice, total in ((1, 1), (2, 5), (5, 5)):
        propio = nombre_de_parte("BASE", indice, total)
        archivo = nombre_de_archivo("BASE", indice, total)
        assert propio == archivo.upper()


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
    assert prefijo_de_busqueda("DP | BITS 18 AUG 2026 05 42") == (
        "DP | BITS 18 AUG 2026 05 42"
    )
    assert prefijo_de_busqueda("  ") == PREFIJO_POR_DEFECTO

"""Ventana temporal en la que puede caer la fecha de una bitácora."""

from __future__ import annotations

from datetime import date

from app.utils.date_window import (
    date_is_possible,
    days_outside_usual,
    is_usual,
    year_is_possible,
    years_outside_usual,
)

HOY = date(2026, 9, 5)


def test_el_ano_de_la_ejecucion_y_los_anteriores_son_posibles():
    assert year_is_possible(2026, HOY)
    assert year_is_possible(2025, HOY)
    assert year_is_possible(2000, HOY)


def test_un_ano_posterior_a_la_ejecucion_no_existe():
    # Una página no se firma después del día en que se escanea: un '96' o
    # un '28' leídos en 2026 son el '26' mal leído.
    assert not year_is_possible(2096, HOY)
    assert not year_is_possible(2028, HOY)
    assert not year_is_possible(1999, HOY)


def test_la_fecha_no_puede_superar_el_dia_de_ejecucion():
    assert date_is_possible(HOY, HOY)
    assert not date_is_possible(date(2026, 9, 6), HOY)
    assert not date_is_possible(date(2026, 12, 31), HOY)
    assert not date_is_possible(date(2027, 1, 1), HOY)


def test_el_mes_actual_y_el_anterior_son_lo_habitual():
    assert is_usual(HOY, HOY)
    assert is_usual(date(2026, 8, 20), HOY)
    assert is_usual(date(2026, 8, 1), HOY)
    assert not is_usual(date(2026, 7, 22), HOY)
    assert not is_usual(date(2026, 6, 1), HOY)


def test_lo_antiguo_se_ordena_por_detras_pero_no_se_descarta():
    assert days_outside_usual(date(2026, 6, 1), HOY) > 0
    assert date_is_possible(date(2020, 7, 25), HOY)


def test_los_anos_fuera_no_se_mueven_por_el_borde_de_la_ventana():
    # Dentro del año en curso todo puntúa igual, dentro y fuera de la
    # ventana habitual, así que el borde no decide nada. Lo que destaca es
    # la página que un año mal leído manda años fuera.
    assert years_outside_usual(date(2026, 8, 20), HOY) == 0
    assert years_outside_usual(date(2026, 6, 1), HOY) == 0
    assert years_outside_usual(date(2024, 8, 20), HOY) == 1
    assert years_outside_usual(date(2020, 8, 20), HOY) == 5

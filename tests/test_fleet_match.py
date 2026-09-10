"""Lecturas ruidosas de matrícula leídas contra la flota completa."""

from pathlib import Path

import pytest

from app.utils.fleet import load_fleet
from app.validation.fleet_match import fleet_match, reading_cost

FLEET = load_fleet(Path(__file__).resolve().parents[1] / "fleet.json")


@pytest.mark.parametrize("raw,expected", [
    # Cinco cifras: el postproceso tomaba las cuatro últimas (8320).
    ("He-18320me", "HP-1832CMP"),
    ("HP-18420M8", "HP-1842CMP"),
    ("1P99160", "HP-9916CMP"),
    # Letras por cifras: I por 1, S por 5, O por 0, Y por 4.
    ("HPIS3OCMP", "HP-1530CMP"),
    ("H0183SC", "HP-1835CMP"),
    ("NP-172YCMP", "HP-1724CMP"),
])
def test_noisy_readings_find_their_aircraft(raw, expected):
    match = fleet_match(raw, FLEET)
    assert match is not None
    assert match[0] == expected


@pytest.mark.parametrize("raw", [
    "4P=1820cnP",   # 1520, 1826 y 1827 igual de cerca
    "10.1030.0",    # 1730 y 1830
    "He-aaod",
    "HP-PBCMP",
    "",
    None,
])
def test_readings_that_do_not_single_out_an_aircraft(raw):
    assert fleet_match(raw, FLEET) is None


def test_a_letter_for_its_digit_costs_less_than_any_other_digit():
    exact = reading_cost("HP-1534CMP", "HP-1534CMP")
    letter = reading_cost("HP-I534CMP", "HP-1534CMP")
    confusable = reading_cost("HP-7534CMP", "HP-1534CMP")
    different = reading_cost("HP-9534CMP", "HP-1534CMP")
    assert exact == 0
    assert letter < confusable < different

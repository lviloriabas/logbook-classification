"""Qué avión de la flota explica una lectura de matrícula.

La lista de flota es el catálogo completo de aviones, así que una lectura no
tiene que acertar sola las cuatro cifras: basta con que se parezca a un avión
bastante más que a los demás. El postproceso de la matrícula no conoce la
flota y elige la ventana de cuatro cifras a ciegas: ``He-18320me`` sale
``HP-8320CMP`` y ``HPIS3OCMP`` se descarta, cuando las dos se leen sin duda
como ``HP-1832CMP`` y ``HP-1530CMP``. Aquí se recorre el texto crudo buscando
el número de cada avión, con lo que cuesta cada carácter distinto.
"""

from __future__ import annotations

import re
from typing import Collection, Optional, Tuple

# Pares de dígitos que el trazo manuscrito de estas bitácoras confunde de
# verdad: el 1 sin base contra el 7 con travesaño, el 2 mal cerrado contra el
# 7, el 3 contra el 8 cuando el lazo se cierra, el 0 contra el 6 y el 9 según
# dónde arranque el trazo. Cambiar uno de estos cuesta menos que cambiar una
# cifra que no se le parece, así que entre dos aviones de la flota que están
# a la misma cantidad de dígitos de distancia gana el que solo pide el trazo
# confundible, que es el error que de verdad comete el reconocedor.
CONFUSABLE_DIGITS = frozenset({
    "17", "27", "12", "14", "47", "49", "07",
    "38", "35", "58", "56", "68", "08", "06", "09",
})
# Costos enteros: comparar distancias en float haría que 0.6+0.6 no empatara
# exacto con 1.2 y un empate real se resolvería por ruido de coma flotante.
DIFFERENT_DIGIT_COST = 10
CONFUSABLE_DIGIT_COST = 6
# El sufijo no se lee de la página: ``apply_postprocess`` lo deduce del número
# con su propia lista de aviones WWP. Por eso cuesta menos que un dígito: si la
# flota trae un WWP que esa lista no conoce, la flota manda y corrige el sufijo.
SUFFIX_COST = 5
# Una letra que el reconocedor devuelve en lugar de esa misma cifra (la O por
# el 0, la S por el 5, la g por el 9) cuesta poco: es la cifra, mal nombrada.
LETTER_COST = 3
# Cualquier otro carácter donde debería ir una cifra.
OTHER_CHAR_COST = 14
# Una lectura se da por un avión si le cuesta a lo sumo esto (tres letras por
# sus cifras, o una cifra confundible y una letra) y el siguiente avión queda
# claramente más lejos. Una cifra que no se parece ya no alcanza sola.
MAX_MATCH_COST = 9
MIN_MATCH_MARGIN = 3

_LETTER_DIGIT = {
    "O": "0", "o": "0", "Q": "0", "D": "0", "U": "0",
    "I": "1", "i": "1", "l": "1", "L": "1", "!": "1", "|": "1",
    "Z": "2", "z": "2",
    "A": "4", "Y": "4", "y": "4",
    "S": "5", "s": "5",
    "G": "6", "b": "6",
    "F": "7", "T": "7", "t": "7", "J": "7",
    "B": "8",
    "g": "9", "q": "9",
}
_MATRICULA_RE = re.compile(r"^HP-(\d{4})(CMP|WWP)$")
_NOISE_RE = re.compile(r"[^A-Za-z0-9!|]")


def digit_cost(left: str, right: str) -> int:
    """Lo que cuesta leer la cifra ``left`` donde está escrita ``right``."""
    if left == right:
        return 0
    pair = "".join(sorted(left + right))
    if pair in CONFUSABLE_DIGITS:
        return CONFUSABLE_DIGIT_COST
    return DIFFERENT_DIGIT_COST


def _char_cost(char: str, digit: str) -> int:
    if char.isdigit():
        return digit_cost(char, digit)
    mapped = _LETTER_DIGIT.get(char)
    if mapped is None:
        return OTHER_CHAR_COST
    return LETTER_COST + digit_cost(mapped, digit)


def reading_cost(text: Optional[str], matricula: str) -> Optional[int]:
    """Lo que cuesta encontrar el número de ``matricula`` dentro de ``text``.

    Se prueba cada tramo de cuatro caracteres, sin separadores, y se queda
    el más barato. ``None`` si el texto no llega a cuatro caracteres o la
    matrícula no tiene formato canónico.
    """
    match = _MATRICULA_RE.fullmatch(matricula)
    compact = _NOISE_RE.sub("", text or "")
    if match is None or len(compact) < 4:
        return None
    digits = match.group(1)
    return min(
        sum(_char_cost(char, digit) for char, digit in zip(compact[i:i + 4], digits))
        for i in range(len(compact) - 3)
    )


def fleet_match(
    text: Optional[str], fleet: Collection[str]
) -> Optional[Tuple[str, int]]:
    """Avión de la flota que la lectura señala sin competencia.

    Devuelve ``(matrícula, costo)`` cuando el más barato cuesta a lo sumo
    :data:`MAX_MATCH_COST` y el segundo queda al menos
    :data:`MIN_MATCH_MARGIN` por detrás; si no, ``None``.
    """
    ranked = []
    for candidate in fleet:
        cost = reading_cost(text, candidate)
        if cost is not None:
            ranked.append((cost, candidate))
    if not ranked:
        return None
    ranked.sort()
    best_cost, best = ranked[0]
    if best_cost > MAX_MATCH_COST:
        return None
    if len(ranked) > 1 and ranked[1][0] - best_cost < MIN_MATCH_MARGIN:
        return None
    return best, best_cost

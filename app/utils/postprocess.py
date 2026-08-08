"""Postprocesadores de valores OCR.

El pipeline aplica el postprocesador cuyo nombre indique la plantilla
(campo ``postprocess``), manteniendo las reglas fuera del código.

Registrados: matricula, date, digits, day, month, year.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Callable, Dict, Optional, Tuple

from loguru import logger

MESES = {
    "ENE": 1, "FEB": 2, "MAR": 3, "ABR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AGO": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DIC": 12,
}
# Meses en español e inglés (para formularios con etiquetas en inglés).
_MESES_LETRAS = {
    "ENE": 1, "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "ABR": 4, "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AGO": 8, "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DIC": 12, "DEC": 12,
}
_CANONICA_MES = {num: nombre
                 for nombre, num in _MESES_LETRAS.items()}
# Abreviaturas de mes (español e inglés) para decodificación por ranuras.
MONTH_WORDS: list = [(nombre, numero)
                     for nombre, numero in _MESES_LETRAS.items()]
# Confusiones típicas de OCR entre dígitos y letras.
_OCR_CHAR_MAP = str.maketrans({"0": "o", "1": "i", "5": "s", "8": "b"})
WWP_ONLY = {"1990", "1522"}
WEAK_MATRICULA_NOTE = (
    "registration: digits inferred from scattered OCR (low confidence)"
)


def _levenshtein(a: str, b: str) -> int:
    """Distancia de Levenshtein entre dos cadenas cortas."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i]
        for j, cb in enumerate(b, start=1):
            cur.append(min(
                cur[-1] + 1,
                prev[j] + 1,
                prev[j - 1] + (ca != cb),
            ))
        prev = cur
    return prev[-1]


def _parse_month(value: str) -> Optional[int]:
    """Número de mes (1-12) a partir de dígitos o letras con fuzzy match.

    Acepta: "7", "07", "JUL", "JUIL", "GUL" (confusión OCR), "JAN",
    "JUL Month" (si las letras dominan). Devuelve None si no es legible.
    """
    raw = value.strip()
    if not raw:
        return None

    if not re.search(r"[A-Za-z]", raw):
        digits = re.sub(r"[^\d]", "", raw)
        if not digits:
            return None
        mes = int(digits[:3])
        return mes if 1 <= mes <= 12 else None

    # El mapa OCR se aplica ANTES de filtrar letras: los dígitos mal leídos
    # ('JU1' -> 'JUI', 'JUL' bien leído) se convierten a su letra probable.
    letters = re.sub(r"[^A-Za-z]", "", raw.upper().translate(_OCR_CHAR_MAP))
    if len(letters) < 2:
        return None
    # Subcadena exacta de un mes dentro de texto impreso ('JULMONTH').
    for nombre, numero in _MESES_LETRAS.items():
        if nombre in letters:
            return numero
    candidatos = [
        (numero, nombre)
        for nombre, numero in _MESES_LETRAS.items()
        if _levenshtein(letters, nombre) < 2
    ]
    if not candidatos:
        return None
    best_dist = min(_levenshtein(letters, nombre) for _, nombre in candidatos)
    empatados = [
        numero for numero, nombre in candidatos
        if _levenshtein(letters, nombre) == best_dist
    ]
    # Desempate determinista: en un empate (p. ej. 'JUi' = JUN o JUL) se
    # prefiere el mes posterior del año. El par JUN/JUL es la confusión
    # típica del formulario: la 'L' de julio pegada al separador de
    # casilla se lee como '1'/'i'.
    return max(empatados)


def _normalize_year(anio: int) -> Optional[int]:
    """Año 4 dígitos en el rango plausible de bitácoras (2000-2100).

    Acepta 2 dígitos (26 → 2026) y rechaza lecturas absurdas
    ("216" → 2216, "1751" → fuera de rango).
    """
    if anio < 100:
        anio += 2000
    return anio if 2000 <= anio <= 2100 else None


def _day(value: str) -> Tuple[str, str]:
    """Día: 1-2 dígitos en el rango 1-31.

    Si la lectura mezcla dígitos (p. ej. '21 0'), se prefiere el run de
    2 dígitos; un run único de 3+ dígitos es una lectura inválida. Dos
    dígitos separados por el separador de casilla impreso (p. ej. '2 0')
    se unen cuando forman un día válido.
    """
    runs = re.findall(r"\d+", value)
    if not runs:
        return "", f"invalid day: {value}"
    if len(runs) == 2 and all(len(r) == 1 for r in runs):
        runs = ["".join(runs)]
    two = next((r for r in runs if len(r) == 2), None)
    if two is not None:
        digits = two
    else:
        one = [r for r in runs if len(r) == 1]
        digits = one[0] if len(one) == 1 else ""
    if not digits or len(digits) > 2:
        return "", f"invalid day: {value}"
    dia = int(digits)
    if not 1 <= dia <= 31:
        return "", f"invalid day: {value}"
    return digits, ""


def _canonical_month(mes: int) -> str:
    """Nombre canónico del mes (español preferido, inglés de respaldo)."""
    for nombre, numero in MESES.items():
        if numero == mes:
            return nombre
    return _CANONICA_MES.get(mes, str(mes))


def _month(value: str) -> Tuple[str, str]:
    """Mes: 1-2 dígitos o 3 letras con fuzzy match.

    Normaliza a las letras canónicas (JUL) si el valor tenía letras, o
    al dígito si era numérico. Nota de "fuzzy" solo si la coincidencia
    vino de la distancia de Levenshtein (no de un nombre exacto).
    """
    raw = value.strip().upper()
    mes = _parse_month(raw)
    if mes is None:
        return "", f"invalid month: {value}"
    had_letters = bool(re.search(r"[A-Za-z]", raw))
    if not had_letters:
        return str(mes), ""
    letters = re.sub(r"[^A-Za-z]", "", raw.upper().translate(_OCR_CHAR_MAP))
    exact = any(nombre in letters for nombre in _MESES_LETRAS)
    note = "" if exact else f"month fuzzy: {value}"
    return _canonical_month(mes), note


def _year(value: str) -> Tuple[str, str]:
    """Año: 2 o 4 dígitos extraídos de una lectura que puede incluir
    etiquetas impresas ('Year YR 26' -> '26').

    Prefiere un run de 4 dígitos en el rango plausible (2000-2100), luego
    el último run de 2; un run de 3 dígitos ('216') se conserva como ERROR
    para que el corrector por libro lo normalice contra el ganador del
    libro. Los runs de 4 dígitos absurdos (p. ej. '8313', '5102', restos
    del log_number) se rechazan devolviendo un valor vacío.

    Los dígitos separados por el separador de casilla impreso se unen:
    dos runs de 1 dígito forman el año de 2 ('2 6' -> '26') y cuatro
    runs de 1 dígito forman el de 4 ('2 0 2 6' -> '2026').
    """
    runs = re.findall(r"\d+", value)
    if not runs:
        return "", f"invalid year: {value}"
    if len(runs) == 2 and all(len(r) == 1 for r in runs):
        runs = ["".join(runs)]
    elif len(runs) == 4 and all(len(r) == 1 for r in runs):
        runs = ["".join(runs)]
    four = next((r for r in runs if len(r) == 4), None)
    if four:
        anio = int(four)
        if 2000 <= anio <= 2100:
            return four, ""
        return "", f"invalid year: {value}"
    twos = [r for r in runs if len(r) == 2]
    if twos:
        return twos[-1], ""
    threes = [r for r in runs if len(r) == 3]
    if threes:
        return threes[-1], f"invalid year: {threes[-1]}"
    return "", f"invalid year: {value}"


def _matricula(value: str) -> Tuple[str, str]:
    """Normaliza la matrícula a HP-XXXXCMP (o HP-XXXXWWP).

    Acepta: 1717, hp1717, 1717cmp, HP-1717, hp-1717-cmp, 1717 CMP...
    Excepciones conocidas (sin CMP): HP-1990WWP, HP-1522WWP.

    La lectura es "débil" cuando el número solo puede reconstruirse con
    dígitos dispersos (p. ej. "wAT 1Hp i712cmp" → 1712); en ese caso el
    pipeline reduce la confianza y el corrector por libro decide.

    Cuando no hay un número de 4 dígitos recuperable, devuelve un valor
    vacío (no el texto crudo del OCR): el corrector por libro lo inferirá
    o la página queda sin detectar, pero la basura nunca llega al CSV.
    """
    raw = re.sub(r"[^A-Z0-9]", "", value.upper())
    match = re.search(r"(?<!\d)\d{4}(?!\d)", raw)
    if match:
        numero = match.group(0)
        note = ""
    else:
        digits = re.findall(r"\d", raw)
        if len(digits) == 4:
            numero = "".join(digits)
            note = WEAK_MATRICULA_NOTE
        else:
            return "", "registration without 4-digit number"
    sufijo = "WWP" if ("WWP" in raw or numero in WWP_ONLY) else "CMP"
    return f"HP-{numero}{sufijo}", note


def _date(value: str) -> Tuple[str, str]:
    """Parsea fechas en múltiples formatos a YYYY/MM/dd.

    Soporta: 15 jul 26 | 15 7 26 | 15-07-26 | 15/07/26 | 15JUL26 |
    07-15-26 (mes/día/año).
    """
    raw = value.strip().upper()
    meses = "|".join(MESES)

    # DD MMM AA / DDMMMAA
    m = re.search(r"(\d{1,2})\s*(" + meses + r")\w*\s*(\d{2,4})", raw)
    if not m:
        m = re.search(r"(\d{1,2})(" + meses + r")(\d{2,4})", raw)
    if m:
        dia, mes, anio = int(m.group(1)), MESES[m.group(2)], int(m.group(3))
        return _build_iso(dia, mes, anio, value)

    # DD/MM/AA | DD-MM-AA | DD MM AA (se prueba día/mes y mes/día)
    m = re.search(
        r"(\d{1,2})\s*[\/\-\s]\s*(\d{1,2})\s*[\/\-\s]\s*(\d{2,4})", raw
    )
    if m:
        a, b, anio = int(m.group(1)), int(m.group(2)), int(m.group(3))
        for dia, mes in ((a, b), (b, a)):
            if 1 <= mes <= 12 and 1 <= dia <= 31:
                iso = _build_iso(dia, mes, anio, value)
                if iso[0] != value:
                    return iso
        return "", f"invalid date: {value}"

    return "", f"unrecognized date: {value}"


def _build_iso(dia: int, mes: int, anio: int,
               original: str) -> Tuple[str, str]:
    anio = _normalize_year(anio)
    if anio is None:
        return original, f"invalid year: {original}"
    try:
        return datetime(anio, mes, dia).strftime("%Y/%m/%d"), ""
    except ValueError:
        return original, f"invalid date: {original}"


def _digits(value: str) -> Tuple[str, str]:
    """Conserva solo dígitos (p. ej. horas o cantidades)."""
    digits = re.sub(r"[^\d]", "", value)
    return (digits, "") if digits else (value, "no digits")


def combine_date(day_value: Optional[str], month_value: Optional[str],
                 year_value: Optional[str]) -> Tuple[str, str]:
    """Combina día/mes/año OCR separados en una fecha YYYY/MM/dd.

    Args:
        day_value: Valor OCR del día (p. ej. "15").
        month_value: Valor OCR del mes (p. ej. "7", "07" o "JUL").
        year_value: Valor OCR del año (p. ej. "26" o "2026").

    Returns:
        (fecha normalizada, nota). La nota vacía indica éxito; si hay
        error, el primer elemento no es una fecha válida.
    """
    if not (day_value and month_value and year_value):
        return year_value or "", "incomplete date"

    d = re.sub(r"[^\d]", "", day_value)
    y = re.sub(r"[^\d]", "", year_value)
    raw = f"{d}/{month_value}/{y}"
    if not (d and y):
        return raw, f"date without digits: {day_value}/{month_value}/{year_value}"

    dia, mes = int(d), _parse_month(month_value)
    if mes is None:
        return raw, f"invalid month: {month_value}"
    anio = _normalize_year(int(y))
    if anio is None:
        return raw, f"invalid year: {year_value}"
    if not 1 <= dia <= 31:
        return raw, f"invalid day: {day_value}"
    try:
        return datetime(anio, mes, dia).strftime("%Y/%m/%d"), ""
    except ValueError:
        return raw, f"invalid date: {day_value}/{month_value}/{year_value}"


POSTPROCESSORS: Dict[str, Callable[[str], Tuple[str, str]]] = {
    "matricula": _matricula,
    "date": _date,
    "digits": _digits,
    "day": _day,
    "month": _month,
    "year": _year,
}


def apply_postprocess(
    field_id: str, name: str, value: str
) -> Tuple[str, str]:
    """Aplica un postprocesador registrado.

    Args:
        field_id: Id del campo (para el log).
        name: Nombre del postprocesador.
        value: Valor OCR crudo.

    Returns:
        (valor procesado, nota). La nota vacía indica éxito.
    """
    processor = POSTPROCESSORS.get(name)
    if processor is None:
        logger.warning(f"Postprocesador '{name}' no registrado "
                       f"(campo {field_id})")
        return value, f"unknown postprocessor: {name}"
    processed, note = processor(value)
    if note:
        logger.debug(f"Postproceso {name} ({field_id}): {note}")
    return processed, note

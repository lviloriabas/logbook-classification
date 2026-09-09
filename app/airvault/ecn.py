"""Categorias consultadas en el catalogo MXDocs con Edge el 8 de septiembre de 2026."""

import re
import unicodedata

CAMPOS_ECN = (9692, 9781, 9782)
_PREFIJO = "DISCREPANCY NOTE: "
RAZON_POR_CAMPO = {
    "captain_signature": _PREFIJO + "MISSING SIGNATURE AND/OR LICENSE NUMBER OF THE CAPTAIN",
    "captain_license": _PREFIJO + "MISSING SIGNATURE AND/OR LICENSE NUMBER OF THE CAPTAIN",
    "pilot_signature": _PREFIJO + "MISSING SIGNATURE AND/OR LICENSE NUMBER OF THE PILOT",
    "technician_signature": _PREFIJO + "MISSING TECHNICIAN SIGNATURE",
    "technician_license": _PREFIJO + "MISSING LICENSE NUMBER",
}


def razones_ecn(campos):
    """Solo ausencias confirmadas; firma y licencia de capitan comparten categoria."""
    return list(dict.fromkeys(RAZON_POR_CAMPO[c] for c in campos if c in RAZON_POR_CAMPO))


def campos_del_resumen(resumen):
    """Compatibilidad con entregas anteriores que solo guardaban la frase generada."""
    texto = "".join(c for c in unicodedata.normalize("NFD", str(resumen or ""))
                    if not unicodedata.combining(c)).lower().strip()
    texto = texto.removeprefix("correccion escrita: ")
    match = re.fullmatch(r"faltan? (.+)", texto)
    if not match:
        return []
    nombres = {
        "firma de piloto": "pilot_signature",
        "firma de capitan": "captain_signature",
        "licencia de capitan": "captain_license",
        "licencia del capitan": "captain_license",
        "firma de tecnico": "technician_signature",
        "licencia de tecnico": "technician_license",
    }
    partes = re.split(r", | y ", match[1])
    return [nombres[p] for p in partes] if all(p in nombres for p in partes) else []


def conservar_razones(valores, remotos):
    """Anade faltas detectadas en espacios libres, conservando anotaciones manuales."""
    pendientes = [valores[c] for c in CAMPOS_ECN if valores.get(c)]
    if not pendientes:
        return valores
    resultado = dict(valores)
    for campo in CAMPOS_ECN:
        resultado.pop(campo, None)
    existentes = {c: str(remotos.get(c, "") or "").strip() for c in CAMPOS_ECN}
    for razon in pendientes:
        if razon in existentes.values():
            continue
        libre = next((c for c, v in existentes.items() if not v), None)
        if libre is None:
            raise ValueError("Los tres campos ECN Reason estan ocupados; revisar las categorias")
        existentes[libre] = razon
    resultado.update({c: v for c, v in existentes.items() if v})
    return resultado

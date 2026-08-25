"""Codificacion de valores tal como los espera el Web Index de AirVault.

El servidor no recibe JSON: espera los valores de indice como una cadena
``fieldId=valor`` separada por tabuladores y codificada en base64, y el
identificador del batch tambien en base64. Aislar esa mecanica aqui deja el
resto del modulo trabajando con diccionarios normales.
"""

from __future__ import annotations

import base64
from typing import Iterable, Mapping

# El separador es un tabulador literal porque ningun valor de indice de
# estas bitacoras puede contenerlo: los campos de texto los rellena el OCR
# con matriculas, numeros y fechas.
_SEPARADOR = "\t"


def codificar_batch_id(batch_id: str) -> str:
    """Devuelve el batchId en base64, como lo pide ``encodedBatchId``."""
    return base64.b64encode(str(batch_id).encode("utf-8")).decode("ascii")


def codificar_valores(valores: Mapping[int, str]) -> str:
    """Codifica ``{fieldId: valor}`` en el ``encodedValues`` del servidor.

    Se conserva el orden de insercion del diccionario: AirVault no lo usa
    para nada, pero mantenerlo estable hace que dos ejecuciones con los mismos
    datos produzcan exactamente la misma peticion, que es lo que permite
    comparar un dry run con lo que se termino enviando.
    """
    partes = [f"{int(campo)}={'' if valor is None else str(valor)}"
              for campo, valor in valores.items()]
    crudo = _SEPARADOR.join(partes)
    return base64.b64encode(crudo.encode("utf-8")).decode("ascii")


def codificar_sticky(campos: Iterable[int]) -> str:
    """Codifica la lista de campos marcados como sticky.

    Sin campos sticky el servidor espera la cadena vacia, no un base64 de
    cadena vacia: mandar ``""`` es lo que hace la propia interfaz.
    """
    ids = [str(int(campo)) for campo in campos]
    if not ids:
        return ""
    return base64.b64encode(
        _SEPARADOR.join(ids).encode("utf-8")
    ).decode("ascii")


def decodificar_valores(encoded: str) -> dict[int, str]:
    """Inversa de :func:`codificar_valores`, usada por los tests y el log."""
    if not encoded:
        return {}
    crudo = base64.b64decode(encoded.encode("ascii")).decode("utf-8")
    resultado: dict[int, str] = {}
    for parte in crudo.split(_SEPARADOR):
        if not parte:
            continue
        campo, _, valor = parte.partition("=")
        try:
            resultado[int(campo)] = valor
        except ValueError:
            continue
    return resultado

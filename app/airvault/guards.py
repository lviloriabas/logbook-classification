"""Guardas previas a escribir en AirVault.

Escribir en la pagina equivocada es el unico error de este modulo que no se
puede deshacer con comodidad: quedaria una bitacora publicada con la
matricula de otro avion. Por eso todas las comprobaciones viven juntas,
son puras y se ejecutan igual en dry run que en automatico. Si alguna falla
el indexado se detiene; nunca escribe "lo que pueda".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Mapping, Sequence

from app.airvault.config import (
    CAMPO_LOG_NUMBER,
    CAMPO_MATRICULA,
    CAMPOS_OBLIGATORIOS,
    ESTADO_VALIDO,
    ESTADO_NECESITA_CORRECCION,
    nombre_campo,
)
from app.airvault.model import Registro


@dataclass(frozen=True)
class Aviso:
    """Un problema detectado, con la pagina a la que corresponde."""

    seq: int
    codigo: str
    detalle: str

    def __str__(self) -> str:
        return f"[{self.codigo}] pagina {self.seq}: {self.detalle}"


class ErrorDeGuarda(RuntimeError):
    """El batch no cumple una condicion que impide escribir nada."""


def verificar_cantidad(
    registros: Sequence[Registro], paginas_lote: int,
    separadores_borrados: int = 0,
) -> None:
    """El manifiesto y el batch tienen que tener las mismas paginas.

    Es la guarda mas importante: si sobran o faltan paginas, la
    correspondencia por posicion esta rota y cualquier escritura cae en la
    bitacora de al lado.

    Los separadores cuentan: en el batch ocupan una pagina cada uno, igual
    que en el PDF que se subio.
    """
    cantidades_validas = {len(registros)}
    if separadores_borrados:
        cantidades_validas.add(len(registros) - separadores_borrados)
    if paginas_lote in cantidades_validas:
        return
    separadores = sum(1 for r in registros if r.es_separador)
    detalle = f", {separadores} de ellos separadores" if separadores else ""
    if paginas_lote <= 0:
        raise ErrorDeGuarda(
            f"AirVault no dijo cuantas paginas tiene el batch, y el manifiesto "
            f"espera {len(registros)}{detalle}. Suele pasar cuando el batch "
            f"todavia se esta procesando en el servidor o cuando el batch "
            f"anotado ya no existe: conviene mirarlo en AirVault antes de "
            f"volver a intentar. No se escribe nada."
        )
    faltan = len(registros) - paginas_lote
    causa = (
        f"al batch le faltan {faltan} paginas de las que trae el PDF"
        if faltan > 0 else
        f"el batch tiene {-faltan} paginas de mas que el PDF de la ejecución"
    )
    raise ErrorDeGuarda(
        f"El batch tiene {paginas_lote} paginas y el manifiesto "
        f"{len(registros)}{detalle}: {causa}. Escribir asi correria cada "
        f"dato a la bitacora de al lado, asi que no se escribe nada. Casi "
        f"siempre es que se subio un PDF distinto al que se preparo, o que "
        f"el batch quedo a medio subir."
    )


def verificar_matriculas(
    registros: Iterable[Registro], picklist: Iterable[str]
) -> List[Aviso]:
    """Toda matricula debe existir en el picklist de AirVault."""
    validas = {str(v).strip().upper() for v in picklist if str(v).strip()}
    avisos: List[Aviso] = []
    for registro in registros:
        if registro.es_separador:
            continue
        if not registro.matricula:
            avisos.append(Aviso(
                registro.seq, "matricula_vacia",
                "no se pudo leer la matricula",
            ))
        elif validas and registro.matricula.upper() not in validas:
            avisos.append(Aviso(
                registro.seq, "matricula_desconocida",
                f"{registro.matricula} no esta en el picklist de AirVault",
            ))
    return avisos


def verificar_obligatorios(
    registro: Registro, valores: Mapping[int, str]
) -> List[Aviso]:
    """Ningun campo obligatorio puede ir vacio.

    El aviso nombra el campo como se llama en la pantalla de AirVault: quien
    lo lee tiene que poder ir a la bitacora y ver que falta, sin traducir un
    numero de campo.
    """
    avisos: List[Aviso] = []
    for campo in CAMPOS_OBLIGATORIOS:
        if not str(valores.get(campo, "")).strip():
            avisos.append(Aviso(
                registro.seq, "obligatorio_vacio",
                f"{nombre_campo(campo)} quedaria vacio y AirVault dejaria "
                f"la pagina en Need Correction",
            ))
    return avisos


def verificar_alineacion(
    registro: Registro, valores_en_airvault: Mapping[int, str],
    permitir_log_distinto: bool = False,
    estado_pagina: int | None = None,
) -> List[Aviso]:
    """Contrasta la pagina del batch con lo que dice el manifiesto.

    Cuando AirVault ya trae un log number para esa pagina, es el mejor
    ancla que existe: si no coincide con el nuestro, o el PDF se subio en
    otro orden o el CSV no corresponde a este batch. En los dos casos hay que
    parar.

    Si AirVault no trae nada, no se puede contrastar y se sigue por
    posicion, que es lo unico disponible.
    """
    avisos: List[Aviso] = []
    log_remoto = str(valores_en_airvault.get(CAMPO_LOG_NUMBER, "")).strip()
    if (
        log_remoto
        and registro.log_number
        and log_remoto != registro.log_number
        and not permitir_log_distinto
    ):
        avisos.append(Aviso(
            registro.seq, "desalineado",
            f"AirVault tiene el log {log_remoto} y el manifiesto "
            f"{registro.log_number}",
        ))
    mat_remota = str(valores_en_airvault.get(CAMPO_MATRICULA, "")).strip()
    if (
        mat_remota
        and registro.matricula
        and mat_remota.upper() != registro.matricula.upper()
        and not log_remoto
        # Quick Upload clasifica inicialmente todas las páginas con el
        # Aircraft de la primera bitácora del archivo. En una página todavía
        # amarilla y sin log ese valor es una preclasificación del batch, no
        # evidencia de que el orden esté corrido. En una página ya verde sí
        # se conserva como guarda: podría ser trabajo previo de una persona.
        and estado_pagina != ESTADO_NECESITA_CORRECCION
    ):
        avisos.append(Aviso(
            registro.seq, "matricula_distinta",
            f"AirVault tiene {mat_remota} y el manifiesto "
            f"{registro.matricula}",
        ))
    return avisos


def verificar_no_pisar(
    registro: Registro, estado_pagina: int, sobrescribir: bool
) -> List[Aviso]:
    """Una pagina ya validada no se toca salvo que se pida expresamente."""
    if estado_pagina == ESTADO_VALIDO and not sobrescribir:
        return [Aviso(
            registro.seq, "ya_indexada",
            "la pagina ya esta en Valid en AirVault; se deja como esta",
        )]
    return []


def verificar_duplicados(registros: Sequence[Registro]) -> List[Aviso]:
    """Dos bitacoras con el mismo log number son un error de lectura."""
    vistos: dict[str, int] = {}
    avisos: List[Aviso] = []
    for registro in registros:
        if registro.es_separador or not registro.log_number:
            continue
        anterior = vistos.get(registro.log_number)
        if anterior is not None:
            avisos.append(Aviso(
                registro.seq, "log_duplicado",
                f"el log {registro.log_number} ya salio en la pagina "
                f"{anterior}",
            ))
        else:
            vistos[registro.log_number] = registro.seq
    return avisos

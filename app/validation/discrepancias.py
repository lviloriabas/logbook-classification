"""Clasificación de páginas con discrepancias (faltas de firma).

Reglas:

La discrepancia es de cada bitácora por separado. El libro no interviene:
dos páginas seguidas del mismo avión pueden ser una de vuelo y otra de
mantenimiento, y cada una se juzga sola.

El tipo lo deciden solo las casillas **limpias**: la licencia de técnico y el
bloque del capitán. ``technician_signature`` queda fuera de esa decisión
porque cae justo debajo de los sellos «MXI Entry Performed By» y «DATE / STA»,
que la llenan de tinta ajena. De 30 páginas revisadas a mano en las que el
detector la daba por escrita, ninguna tenía firma. Un sello solo añade tinta y
nunca la quita, así que su lectura «ausente» sigue siendo de fiar: el campo se
conserva como requisito de mantenimiento, pero no puede decidir el tipo.

- Una página es de **mantenimiento** cuando ``technician_license`` está
  escrita de forma confiable.
- Es de **vuelo** cuando esa licencia está vacía y hay algo escrito en el
  bloque del capitán o en la firma del piloto.
- Es una hoja **anulada** (VOID) cuando la licencia de técnico, las dos
  casillas del capitán y la firma del piloto están vacías. Se llenó mal, se
  apartó y lleva el log page y a veces la matrícula, nada más. No le falta
  ninguna firma porque no llegó a usarse: se indexa como cualquier otra y no
  abre discrepancia.
- Si ninguna casilla limpia lo dice con seguridad, el tipo es **incierto**
  (INCIERTO): se reportan solo las anomalías robustas (firma de piloto,
  exigida en las dos interpretaciones) y las casillas ilegibles que impiden
  decidir. El resto es ambiguo: acusarlo produciría discrepancias falsas.
- **Vuelo**: deben estar presentes la firma de piloto, la firma de capitán y
  la licencia del capitán.
- **Mantenimiento**: deben estar presentes la firma de piloto, la firma de
  técnico y la licencia del técnico. La firma y la licencia de capitán no se
  miran: el formulario F-MNT-001 es uno solo y lleva el bloque de
  mantenimiento («MAINTENANCE RETURN TO SERVICE» + «MECH. LICENSE No.») y el
  de aceptación de la aeronave («MAINTENANCE CHECK AIRWORTHINESS RELEASE»,
  que firma el capitán) en la misma hoja. Que estén los dos es lo normal, no
  una anomalía.

De las dos licencias solo se mira si la casilla está escrita o vacía. No se
lee lo que dice: el número no forma parte del índice ni de la regla.

La "presencia" de una firma se decide con el resultado del detector
(``true`` / ``false`` / ``unclear``) combinado con la confianza y los
umbrales del campo (``sig_present_conf`` / ``sig_absent_conf``): una
lectura de baja confianza nunca se acusa como falta (evita discrepancias
falsas) y se marca como *incierta* (categoría UNCERTAIN, revisión manual).

Las discrepancias se ordenan globalmente por número de bitácora
(``log_number``) ascendente, sin subdividirlas por matrícula o mes.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import List, Optional, Tuple

from loguru import logger
from pydantic import BaseModel, Field

from app.models.schemas import FieldResult, PageResult, ValidationReport
from app.templates.schema import Template
from app.validation.grouping import log_number

FIELD_PILOT = "pilot_signature"
FIELD_CAPTAIN = "captain_signature"
FIELD_CAPTAIN_LICENSE = "captain_license"
FIELD_TECH = "technician_signature"
FIELD_TECH_LICENSE = "technician_license"

_MATRICULA_RE = re.compile(r"^HP-\d{4}(CMP|WWP)$")

# Cómo se nombra cada casilla cuando es ella la que impide decidir el tipo.
_NOMBRE_ILEGIBLE = {
    FIELD_TECH_LICENSE: "Licencia de técnico",
    FIELD_CAPTAIN: "Firma de capitán",
    FIELD_CAPTAIN_LICENSE: "Licencia del capitán",
}


class TipoEntrada(str, Enum):
    """Tipo de entrada de la bitácora."""

    VUELO = "vuelo"
    MANTENIMIENTO = "mantenimiento"
    INCIERTO = "incierto"


class Categoria(str, Enum):
    """Categoría de la discrepancia."""

    MISSING = "missing"      # firma faltante confirmada
    UNCERTAIN = "uncertain"  # firma incierta (revisión manual)


class CampoAfectado(BaseModel):
    """Campo de firma afectado, con su categoría y razón legible."""

    field_id: str
    categoria: Categoria
    razon: str


class Discrepancia(BaseModel):
    """Página con discrepancia de firmas."""

    pdf_path: str
    page_number: int
    matricula: Optional[str] = None
    log_number: Optional[int] = None
    tipo: TipoEntrada
    categoria: Categoria
    campos: List[CampoAfectado] = Field(default_factory=list)

    def razones(self) -> List[str]:
        """Razones legibles de la discrepancia, en orden de importancia."""
        return [campo.razon for campo in self.campos]


def _campo(page: PageResult, field_id: str) -> Optional[FieldResult]:
    for field in page.fields:
        if field.field_id == field_id:
            return field
    return None


def _presencia(campo: Optional[FieldResult], field_template) -> Optional[bool]:
    """Presencia de una firma: True, False o None (incierta).

    El umbral se toma del campo de la plantilla:
    - ``true`` con confianza >= ``sig_present_conf``  -> presente.
    - ``false`` con confianza >= ``sig_absent_conf``  -> ausente.
    - Cualquier otro caso (``unclear``, confianza baja) -> incierta.
    """
    if campo is None or not campo.value:
        return None
    if campo.value == "true" and campo.confidence >= field_template.sig_present_conf:
        return True
    if campo.value == "false" and campo.confidence >= field_template.sig_absent_conf:
        return False
    return None


def _campo_presente(
    page: PageResult, template: Template, field_id: str
) -> Optional[bool]:
    """Presencia de un campo de firma, o ``False`` si no está en la plantilla.

    Un campo que el esquema no define no puede estar escrito, así que no
    aporta evidencia de que la entrada sea de mantenimiento.
    """
    plantilla = template.field(field_id)
    if plantilla is None:
        return False
    return _presencia(_campo(page, field_id), plantilla)


def _matricula(page: PageResult) -> Optional[str]:
    """Matrícula corregida de la página (canónica HP-XXXXCMP/WWP), o None."""
    campo = _campo(page, "matricula")
    if campo is None or not campo.value:
        return None
    value = campo.value.strip()
    return value if _MATRICULA_RE.fullmatch(value) else None


def _clasificar_pagina(page: PageResult, template: Template
                       ) -> Optional[Tuple[TipoEntrada, Categoria,
                                           List[CampoAfectado]]]:
    """Clasifica una página y devuelve sus campos de firma afectados.

    Returns:
        (tipo, categoria, campos afectados) si hay discrepancia,
        o None si la página cumple todas las firmas requeridas.
    """
    licencia_tecnico = _campo_presente(page, template, FIELD_TECH_LICENSE)
    firma_capitan = _campo_presente(page, template, FIELD_CAPTAIN)
    licencia_capitan = _campo_presente(page, template, FIELD_CAPTAIN_LICENSE)
    firma_piloto = _campo_presente(page, template, FIELD_PILOT)

    # Una bitácora VOID se anuló al llenarla y se apartó: lleva el log page y
    # a veces la matrícula, y nada más. No le falta ninguna firma porque no
    # llegó a usarse, así que se indexa como cualquier otra y no abre
    # discrepancia. Se reconoce porque ninguna de las casillas fiables tiene
    # nada. La firma de técnico no entra en la comprobación a propósito: un
    # sello sobre ella no convierte una hoja anulada en una discrepancia.
    if (licencia_tecnico is False and firma_capitan is False
            and licencia_capitan is False and firma_piloto is False):
        return None

    # El tipo lo deciden solo las casillas limpias. La de firma de técnico
    # queda fuera de esta decisión: cae justo debajo de los sellos «MXI Entry
    # Performed By» y «DATE / STA», que la llenan de tinta ajena, y sobre 30
    # páginas revisadas a mano ninguna tenía firma. Un sello solo añade tinta,
    # nunca la quita, así que su lectura «ausente» sigue siendo de fiar y por
    # eso el campo se conserva como requisito de mantenimiento; lo que no
    # soporta es decidir de qué tipo es la bitácora.
    if licencia_tecnico is True:
        tipo = TipoEntrada.MANTENIMIENTO
        requisitos = [
            ("Falta firma de piloto (entrada de mantenimiento)",
             "Firma de piloto incierta (entrada de mantenimiento); revisar",
             FIELD_PILOT),
            ("Falta firma de técnico (entrada de mantenimiento)",
             "Firma de técnico incierta (entrada de mantenimiento); revisar",
             FIELD_TECH),
            ("Falta licencia de técnico (entrada de mantenimiento)",
             "Licencia de técnico incierta (entrada de mantenimiento); revisar",
             FIELD_TECH_LICENSE),
        ]
    elif licencia_tecnico is False and True in (
        firma_capitan, licencia_capitan, firma_piloto
    ):
        tipo = TipoEntrada.VUELO
        requisitos = [
            ("Falta firma de piloto",
             "Firma de piloto incierta; revisar", FIELD_PILOT),
            ("Falta firma de capitán",
             "Firma de capitán incierta; revisar", FIELD_CAPTAIN),
            ("Falta licencia del capitán",
             "Firma de licencia del capitán incierta; revisar",
             FIELD_CAPTAIN_LICENSE),
        ]
    else:
        # Ninguna casilla limpia dice con seguridad de qué tipo es la
        # bitácora: no se puede decidir entre vuelo, mantenimiento y hoja
        # anulada. Solo se reportan anomalías robustas (firma de piloto,
        # exigida en las dos interpretaciones) y las casillas ilegibles que
        # impiden decidir. El resto es ambiguo: acusarlo produciría
        # discrepancias falsas.
        tipo = TipoEntrada.INCIERTO
        afectados: List[CampoAfectado] = [
            CampoAfectado(
                field_id=field_id,
                categoria=Categoria.UNCERTAIN,
                razon=(
                    f"{_NOMBRE_ILEGIBLE[field_id]} ilegible; no se pudo "
                    "determinar de qué tipo es la bitácora"
                ),
            )
            for field_id, presencia in (
                (FIELD_TECH_LICENSE, licencia_tecnico),
                (FIELD_CAPTAIN, firma_capitan),
                (FIELD_CAPTAIN_LICENSE, licencia_capitan),
            )
            if presencia is None
        ]
        tmpl_piloto = template.field(FIELD_PILOT)
        if tmpl_piloto is not None:
            presencia = _presencia(_campo(page, FIELD_PILOT), tmpl_piloto)
            if presencia is None:
                afectados.append(CampoAfectado(
                    field_id=FIELD_PILOT, categoria=Categoria.UNCERTAIN,
                    razon="Firma de piloto incierta (tipo de página incierto); "
                          "revisar",
                ))
            elif presencia is False:
                afectados.append(CampoAfectado(
                    field_id=FIELD_PILOT, categoria=Categoria.MISSING,
                    razon="Falta firma de piloto (tipo de página incierto)",
                ))
        categoria = (
            Categoria.MISSING
            if any(a.categoria is Categoria.MISSING for a in afectados)
            else Categoria.UNCERTAIN
        )
        return tipo, categoria, afectados

    afectados: List[CampoAfectado] = []
    for razon_missing, razon_uncertain, field_id in requisitos:
        tmpl = template.field(field_id)
        if tmpl is None:
            continue
        presencia = _presencia(_campo(page, field_id), tmpl)
        if presencia is None:
            afectados.append(CampoAfectado(
                field_id=field_id, categoria=Categoria.UNCERTAIN,
                razon=razon_uncertain,
            ))
        elif presencia is False:
            afectados.append(CampoAfectado(
                field_id=field_id, categoria=Categoria.MISSING,
                razon=razon_missing,
            ))

    if not afectados:
        return None
    categoria = (
        Categoria.MISSING
        if any(a.categoria is Categoria.MISSING for a in afectados)
        else Categoria.UNCERTAIN
    )
    return tipo, categoria, afectados


def clasificar_lote(reports: List[ValidationReport], template: Template
                    ) -> List[Discrepancia]:
    """Clasifica todas las páginas del batch y devuelve las discrepancias.

    Marca ``page.discrepancy`` solo en las páginas con una ausencia
    confirmada. Las lecturas inciertas se devuelven igual, y con ellas se
    escribe el reporte de discrepancias, pero no llevan marca: ninguna firma
    es un index field, así que una firma ilegible no puede estropear lo que
    se escribe en AirVault, y apartar esa página costaría teclear a mano seis
    campos que ya están resueltos.

    El resultado va ordenado globalmente por ``log_number`` ascendente
    (libro + logpage), y dentro del mismo número por archivo/página.
    """
    entradas: List[Discrepancia] = []
    for report in reports:
        for page in report.pages:
            page.discrepancy = False
            if page.blank:
                continue
            resultado = _clasificar_pagina(page, template)
            if resultado is None:
                continue
            tipo, categoria, campos = resultado
            page.discrepancy = categoria is Categoria.MISSING
            entradas.append(Discrepancia(
                pdf_path=str(report.pdf_path),
                page_number=page.page_number,
                matricula=_matricula(page),
                log_number=log_number(page),
                tipo=tipo,
                categoria=categoria,
                campos=campos,
            ))

    entradas.sort(key=lambda d: (
        d.log_number if d.log_number is not None else 1 << 30,
        d.pdf_path,
        d.page_number,
    ))
    logger.info(f"[Discrepancias] {len(entradas)} página(s) con discrepancias")
    return entradas


def confirmadas_para_revision(
    entradas: List[Discrepancia],
) -> List[Discrepancia]:
    """Devuelve solo ausencias confirmadas para la revisión manual.

    Las lecturas ``UNCERTAIN`` conservan su detalle en el reporte de
    discrepancias, pero no salen del flujo automático ni llevan
    ``page.discrepancy``. Solo ``MISSING`` confirma que falta una firma
    exigida y justifica separar la página.
    """
    return [
        entrada
        for entrada in entradas
        if entrada.categoria is Categoria.MISSING
    ]

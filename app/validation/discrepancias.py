"""Clasificación de páginas con discrepancias (faltas de firma).

Reglas:

- Una página es de **mantenimiento** cuando el campo ``technician_license``
  está presente de forma confiable.
- Si la licencia del técnico está ausente de forma confiable se trata como
  **vuelo**.
- Si la licencia del técnico es ilegible/incierta, el tipo de página es
  **incierto** (INCIERTO): no se acusa a los campos que solo aplican a una
  de las dos interpretaciones (capitán/licencia de capitán y técnico), se
  reportan solo las anomalías robustas —firma de piloto, exigida en ambos
  casos— y la propia licencia ilegible. Evita tanto las discrepancias
  falsas de vuelo como las de mantenimiento cuando no se puede decidir.
- **Vuelo**: deben estar presentes las firmas de piloto, de capitán y la
  licencia del capitán.
- **Mantenimiento**: deben estar presentes las firmas de piloto y de técnico.

La "presencia" de una firma se decide con el resultado del detector
(``true`` / ``false`` / ``unclear``) combinado con la confianza y los
umbrales del campo (``sig_present_conf`` / ``sig_absent_conf``): una
lectura de baja confianza nunca se acusa como falta (evita discrepancias
falsas) y se marca como *incierta* (categoría UNCERTAIN, revisión manual).

Las discrepancias se ordenan por avión (matrícula corregida) y luego por
número de bitácora (``log_number``) ascendente.
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
    tech_license = _campo(page, FIELD_TECH_LICENSE)
    tech_license_tmpl = template.field(FIELD_TECH_LICENSE)
    if tech_license_tmpl is None:
        # Plantilla sin campo de licencia de técnico: no puede haber
        # mantenimiento en este esquema.
        tech_pres: Optional[bool] = False
    else:
        tech_pres = _presencia(tech_license, tech_license_tmpl)

    if tech_pres is True:
        tipo = TipoEntrada.MANTENIMIENTO
        requisitos = [
            ("Falta firma de piloto (entrada de mantenimiento)",
             "Firma de piloto incierta (entrada de mantenimiento); revisar",
             FIELD_PILOT),
            ("Falta firma de técnico (entrada de mantenimiento)",
             "Firma de técnico incierta (entrada de mantenimiento); revisar",
             FIELD_TECH),
        ]
    elif tech_pres is False:
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
        # Licencia de técnico ilegible: no se puede decidir entre vuelo y
        # mantenimiento. Solo se reportan anomalías robustas (firma de
        # piloto, exigida en ambas interpretaciones) y la propia licencia
        # ilegible para su revisión manual. El resto (capitán, técnico) es
        # ambiguo: acusarlo produciría discrepancias falsas.
        tipo = TipoEntrada.INCIERTO
        afectados: List[CampoAfectado] = [
            CampoAfectado(
                field_id=FIELD_TECH_LICENSE,
                categoria=Categoria.UNCERTAIN,
                razon=(
                    "Licencia de técnico ilegible; no se pudo determinar "
                    "si la entrada es de mantenimiento"
                ),
            )
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
    """Clasifica todas las páginas del lote y devuelve las discrepancias.

    Marca ``page.discrepancy`` en las páginas afectadas y ordena el
    resultado por avión (matrícula) y luego por ``log_number`` ascendente
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
            page.discrepancy = True
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
        d.matricula or "\uffff",
        d.log_number if d.log_number is not None else 1 << 30,
        d.pdf_path,
        d.page_number,
    ))
    logger.info(f"[Discrepancias] {len(entradas)} página(s) con discrepancias")
    return entradas

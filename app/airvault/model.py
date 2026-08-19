"""Modelo del trabajo de indexado: registros y manifiesto.

El manifiesto es la unica fuente de verdad del trabajo. Cada etapa lo lee,
hace lo suyo y lo vuelve a escribir, de modo que un proceso interrumpido se
retoma donde quedo y una etapa se puede correr sola sin volver a ejecutar
las anteriores.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import ClassVar, Dict, List, Optional

from pydantic import BaseModel, Field


class EstadoEtapa(str, Enum):
    """Estado de una etapa del trabajo."""

    PENDIENTE = "pendiente"
    EN_CURSO = "en_curso"
    HECHA = "hecha"
    ERROR = "error"
    OMITIDA = "omitida"


class EstadoRegistro(str, Enum):
    """Estado de una bitacora dentro del trabajo."""

    PENDIENTE = "pendiente"
    ESCRITA = "escrita"
    OMITIDA = "omitida"
    ERROR = "error"


class Etapa(BaseModel):
    """Una etapa del trabajo con su estado y su ultimo mensaje."""

    estado: EstadoEtapa = EstadoEtapa.PENDIENTE
    actualizada: Optional[str] = None
    detalle: str = ""

    def marcar(self, estado: EstadoEtapa, detalle: str = "") -> None:
        self.estado = estado
        self.detalle = detalle
        self.actualizada = datetime.now().isoformat(timespec="seconds")


class Registro(BaseModel):
    """Una bitacora: de donde salio, que se le va a escribir y como quedo."""

    # Posicion dentro del artefacto que se sube. Es la que debe coincidir
    # con la pagina del lote en AirVault cuando el lote se arma con el PDF
    # ordenado de la corrida.
    seq: int
    archivo_origen: str = ""
    pagina_origen: int = 0

    # Etiqueta del separador cuando esta posicion del lote no es una
    # bitacora sino una pagina divisoria del PDF de entrega («REVISAR»,
    # «POSIBLES DISCREPANCIAS», la matricula de un grupo). Ocupa su lugar en
    # el lote para que la correspondencia por posicion siga en pie, pero no
    # se le escribe nada: no es un documento que indexar.
    separador: str = ""

    matricula: str = ""
    log_number: str = ""
    # Fecha en el formato del CSV de la corrida (YYYY/MM/dd). La conversion
    # al formato de AirVault (m/d/Y) se hace al construir los valores, no
    # aqui, para que el manifiesto se siga leyendo igual que el CSV.
    fecha: str = ""
    fleet: str = ""
    lessor: str = ""
    fleet_inferido: bool = False

    duplicado: bool = False
    discrepancia: bool = False

    pagina_batch: Optional[int] = None
    estado: EstadoRegistro = EstadoRegistro.PENDIENTE
    avisos: List[str] = Field(default_factory=list)

    @property
    def es_separador(self) -> bool:
        return bool(self.separador)

    def listo_para_escribir(self) -> bool:
        if self.es_separador:
            return False
        return self.estado is EstadoRegistro.PENDIENTE and not self.avisos


class Manifiesto(BaseModel):
    """Estado completo de un trabajo de indexado."""

    version: int = 1
    job_id: str
    creado: str = Field(
        default_factory=lambda: datetime.now().isoformat(timespec="seconds")
    )
    nombre_batch: str = ""
    repo_id: int = 3209
    batch_id: Optional[str] = None
    csv_origen: str = ""
    # Archivo de entrega que forma este lote. Una corrida repartida en
    # partes tiene un manifiesto por parte, cada uno con su PDF y su lote.
    pdf_origen: str = ""
    parte: int = 1
    partes: int = 1
    # El lote se sube pero no se indexa: recoge las bitacoras sin avion
    # confirmado, que nadie puede archivar sin mirarlas una por una.
    solo_subir: bool = False

    doc_type: str = "Log Page"
    audit_status: str = "PUBLISHED"

    etapas: Dict[str, Etapa] = Field(default_factory=dict)
    registros: List[Registro] = Field(default_factory=list)

    # Orden canonico de las etapas. Es una constante de clase, no un campo:
    # no tiene por que viajar en el JSON del manifiesto ni cambiar por
    # trabajo.
    ORDEN_ETAPAS: ClassVar[tuple[str, ...]] = (
        "procesar", "preparar", "subir", "descubrir", "indexar", "verificar",
    )

    def etapa(self, nombre: str) -> Etapa:
        """Devuelve la etapa, creandola en pendiente si no existia."""
        if nombre not in self.etapas:
            self.etapas[nombre] = Etapa()
        return self.etapas[nombre]

    def etapa_hecha(self, nombre: str) -> bool:
        etapa = self.etapas.get(nombre)
        return etapa is not None and etapa.estado in (
            EstadoEtapa.HECHA, EstadoEtapa.OMITIDA
        )

    def etapas_previas(self, nombre: str) -> List[str]:
        """Etapas que deben estar hechas antes de correr ``nombre``."""
        if nombre not in self.ORDEN_ETAPAS:
            return []
        return list(self.ORDEN_ETAPAS[: self.ORDEN_ETAPAS.index(nombre)])

    def pendientes(self) -> List[Registro]:
        return [r for r in self.registros
                if r.estado is EstadoRegistro.PENDIENTE
                and not r.es_separador]

    def escritos(self) -> List[Registro]:
        return [r for r in self.registros
                if r.estado is EstadoRegistro.ESCRITA]

    def con_avisos(self) -> List[Registro]:
        return [r for r in self.registros if r.avisos]

    def bitacoras(self) -> List[Registro]:
        """Registros que son documentos, sin las paginas divisorias."""
        return [r for r in self.registros if not r.es_separador]

    def separadores(self) -> List[Registro]:
        return [r for r in self.registros if r.es_separador]

    def resumen(self) -> Dict[str, int]:
        return {
            "registros": len(self.registros),
            "separadores": len(self.separadores()),
            "pendientes": len(self.pendientes()),
            "escritos": len(self.escritos()),
            "con_avisos": len(self.con_avisos()),
            "con_error": sum(
                1 for r in self.registros
                if r.estado is EstadoRegistro.ERROR
            ),
        }

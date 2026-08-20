"""Modelos de dominio compartidos por toda la aplicación."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class Status(str, Enum):
    """Estado de validación de una página o campo."""

    OK = "OK"
    WARNING = "WARNING"
    ERROR = "ERROR"


class OcrResult(BaseModel):
    """Línea de texto reconocida por el motor OCR."""

    text: str
    confidence: float = 0.0
    box: Optional[List[List[float]]] = None


class InkAnalysis(BaseModel):
    """Análisis de tinta de una región (para firmas/checkbox)."""

    ink_ratio: float = 0.0
    component_count: int = 0
    max_component_area: float = 0.0


class FieldResult(BaseModel):
    """Resultado de la extracción/validación de un campo en una página."""

    page_number: int
    field_id: str
    field_type: str
    value: Optional[str] = None
    raw_value: Optional[str] = None
    confidence: float = 0.0
    status: Status = Status.OK
    comment: str = ""
    source: str = "direct"
    inference_method: Optional[str] = None
    alternatives: List[str] = Field(default_factory=list)
    # Cuantas lecturas independientes respaldan este valor exacto. Solo lo
    # llenan los correctores que imponen un valor que la pagina no leyo:
    # None significa que el valor es la lectura de la propia pagina. Con 0 o
    # 1 el valor se sostiene en una sola lectura de todo el libro (o en
    # ninguna, si se recompuso), y eso no alcanza para indexarlo sin mirar.
    votes: Optional[int] = None


class PageResult(BaseModel):
    """Resultado del procesamiento de una página completa."""

    page_number: int
    status: Status = Status.OK
    blank: bool = False
    skew_angle: float = 0.0
    alignment_quality: str = "ok"
    processing_ms: float = 0.0
    discrepancy: bool = False
    date: Optional[str] = None
    fields: List[FieldResult] = Field(default_factory=list)
    comment: str = ""
    # Metadatos efímeros del visor. Viajan desde los workers hasta la GUI,
    # pero no forman parte del JSON ni de ningún reporte persistente.
    preview_alignment: Optional[Dict[str, float]] = Field(
        default=None, exclude=True, repr=False
    )
    preview_boxes: Dict[str, List[float]] = Field(
        default_factory=dict, exclude=True, repr=False
    )

    def add_field(self, field: FieldResult) -> None:
        """Agrega un campo y recalcula el estado de la página."""
        self.fields.append(field)

    def worst_status(self) -> Status:
        """Devuelve el peor estado entre los campos de la página."""
        if self.status is not Status.OK:
            return self.status
        order = {Status.OK: 0, Status.WARNING: 1, Status.ERROR: 2}
        worst = max((f.status for f in self.fields), key=order.get,
                    default=Status.OK)
        return worst


class ValidationReport(BaseModel):
    """Reporte completo de validación de un documento PDF."""

    pdf_path: str
    template_name: str
    generated_at: str = Field(
        default_factory=lambda: datetime.now().isoformat(timespec="seconds")
    )
    processing_ms: float = 0.0
    calibration_ms: float = 0.0
    # Instante de arranque (epoch, ``time.time()``) para poder medir el reloj
    # real de un lote: con un proceso por archivo las bitácoras se solapan y
    # sumar ``processing_ms`` cuenta el mismo minuto una vez por archivo. No
    # viaja al JSON: es metadato de la corrida, no del reporte.
    started_at: float = Field(default=0.0, exclude=True, repr=False)
    cancelled: bool = False
    summary: Dict[str, int] = Field(default_factory=dict)
    pages: List[PageResult] = Field(default_factory=list)

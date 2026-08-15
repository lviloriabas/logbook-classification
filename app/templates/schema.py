"""Esquemas pydantic del sistema de plantillas."""

from __future__ import annotations

import re
from enum import Enum
from typing import List, Optional, Tuple

from pydantic import BaseModel, Field, field_validator, model_validator


class FieldType(str, Enum):
    """Tipos de campo soportados por la plantilla."""

    OCR = "ocr"
    TEXT = "text"
    DATE = "date"
    SIGNATURE = "signature"
    CHECKBOX = "checkbox"


class OcrMode(str, Enum):
    """Cómo se lee el recorte de un campo con PaddleOCR (solo CPU).

    ``detect`` ejecuta el detector de texto y luego el reconocedor. El
    detector localiza la escritura dentro de la casilla, así que tolera
    bordes impresos, rótulos y márgenes sobrantes.

    ``line`` salta el detector y envía el recorte completo al reconocedor,
    que lo trata como una sola línea de texto. Medido sobre los recortes
    reales del pipeline: 646 ms por recorte con detector frente a 175 ms
    sin él (3.7x). Solo es equivalente cuando el recorte ya contiene
    exactamente un valor y nada más; en casillas con rótulo impreso o
    varias palabras el detector sigue siendo necesario.
    """

    DETECT = "detect"
    LINE = "line"


class FieldTemplate(BaseModel):
    """Definición de un campo de la plantilla (coordenadas relativas 0-1)."""

    id: str
    type: FieldType = FieldType.OCR
    required: bool = False
    x: float = Field(ge=0.0, le=1.0, description="left relativa")
    y: float = Field(ge=0.0, le=1.0, description="top relativa")
    w: float = Field(gt=0.0, le=1.0, description="ancho relativo")
    h: float = Field(gt=0.0, le=1.0, description="alto relativo")
    regex: Optional[str] = None
    min_length: Optional[int] = Field(default=None, ge=0)
    max_length: Optional[int] = Field(default=None, ge=0)
    postprocess: Optional[str] = Field(
        default=None,
        description="Nombre de un postprocesador (matricula, date, digits)",
    )
    localize: Optional[str] = Field(
        default=None,
        description="Modo de localización antes del OCR: 'ink' sub-recorta "
                    "la región al extento de la tinta manuscrita",
    )
    ocr_mode: OcrMode = Field(
        default=OcrMode.DETECT,
        description="'detect' ejecuta detector + reconocedor (por defecto); "
                    "'line' salta el detector y lee el recorte como una "
                    "sola línea (3.7x más rápido, solo válido cuando el "
                    "recorte contiene un único valor)",
    )
    min_ink_ratio: float = Field(default=0.02, ge=0.0, le=1.0)
    max_ink_ratio: float = Field(default=0.90, ge=0.0, le=1.0)
    min_components: int = Field(default=2, ge=0)
    ink_delta: float = Field(
        default=80.0, ge=1.0, le=255.0,
        description="cuánto más oscuro que su propio papel debe ser un píxel "
                    "para contar como tinta de firma; al medirse contra el "
                    "fondo local no depende del gris de la fotocopia",
    )
    min_ink_peak: float = Field(
        default=0.12, ge=0.0, le=1.0,
        description="densidad local de tinta (fracción de una ventana del "
                    "alto del campo) a partir de la cual se declara la firma "
                    "presente",
    )
    max_empty_peak: float = Field(
        default=0.05, ge=0.0, le=1.0,
        description="densidad local de tinta por debajo de la cual el campo "
                    "se considera vacío; entre este umbral y min_ink_peak la "
                    "firma queda incierta",
    )
    min_ink_span: float = Field(
        default=0.30, ge=0.0, le=1.0,
        description="fracción del ancho con tinta que basta para dar por "
                    "presente una escritura poco densa pero repartida "
                    "(números de licencia manuscritos)",
    )
    sig_present_conf: float = Field(
        default=0.45, ge=0.0, le=1.0,
        description="confianza mínima para confiar en que una firma está presente",
    )
    sig_absent_conf: float = Field(
        default=0.55, ge=0.0, le=1.0,
        description="confianza mínima para confiar en que una firma está ausente "
                     "(región limpia); por debajo se trata como incierta",
    )

    @model_validator(mode="after")
    def _validate_geometry(self) -> "FieldTemplate":
        """Evita que un campo salga del lienzo de la plantilla."""
        if self.x + self.w > 1.0:
            raise ValueError(
                f"campo '{self.id}' excede el ancho de la página "
                f"(x + w = {self.x + self.w:.6f})"
            )
        if self.y + self.h > 1.0:
            raise ValueError(
                f"campo '{self.id}' excede el alto de la página "
                f"(y + h = {self.y + self.h:.6f})"
            )
        if (
            self.type is FieldType.SIGNATURE
            and self.max_empty_peak > self.min_ink_peak
        ):
            raise ValueError(
                f"campo '{self.id}' tiene umbrales de firma invertidos "
                f"(max_empty_peak {self.max_empty_peak} > "
                f"min_ink_peak {self.min_ink_peak})"
            )
        return self

    @field_validator("regex")
    @classmethod
    def _validate_regex(cls, value: Optional[str]) -> Optional[str]:
        if value:
            try:
                re.compile(value)
            except re.error as exc:
                raise ValueError(f"regex inválida: {exc}") from exc
        return value

    def rect_pixels(self, width: int, height: int) -> Tuple[int, int, int, int]:
        """Convierte el rectángulo relativo a píxeles (left, top, right, bottom)."""
        left = int(self.x * width)
        top = int(self.y * height)
        right = int((self.x + self.w) * width)
        bottom = int((self.y + self.h) * height)
        return left, top, right, bottom


class Template(BaseModel):
    """Plantilla completa de un formulario."""

    name: str
    version: str = "1.0"
    page_size: List[int] = Field(default_factory=lambda: [2480, 3508])
    fields: List[FieldTemplate] = Field(default_factory=list)

    @field_validator("fields")
    @classmethod
    def _validate_unique_field_ids(
        cls, fields: List[FieldTemplate]
    ) -> List[FieldTemplate]:
        """Los IDs son claves de resultado y no pueden sobrescribirse."""
        ids = [field.id for field in fields]
        duplicates = sorted({field_id for field_id in ids if ids.count(field_id) > 1})
        if duplicates:
            raise ValueError(
                "IDs de campo duplicados: " + ", ".join(duplicates)
            )
        return fields

    def field(self, field_id: str) -> Optional[FieldTemplate]:
        """Devuelve un campo por su id, o None si no existe."""
        for field in self.fields:
            if field.id == field_id:
                return field
        return None

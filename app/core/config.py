"""Configuración global de la aplicación (pydantic)."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field


class AppConfig(BaseModel):
    """Parámetros de configuración del pipeline."""

    dpi: int = Field(default=200, ge=72, le=600,
                     description="DPI del renderizado PDF→imagen")
    blank_threshold: float = Field(
        default=15.0, ge=0.0,
        description="Varianza de grises bajo la cual la página se considera vacía",
    )
    deskew: bool = True
    align: bool = True
    remove_printed: bool = Field(
        default=True,
        description="Construir un mapa del fondo impreso idéntico en todas "
                    "las páginas para análisis de firmas y ranuras de fecha. "
                    "El OCR conserva la imagen original porque la escritura "
                    "repetida también puede aparecer en el consenso. Se "
                    "activa solo con >= 3 páginas por archivo.",
    )
    printed_ink_threshold: int = Field(
        default=185, ge=0, le=255,
        description="Umbral de gris para el mapa del fondo impreso: los "
                    "píxeles con gris < este valor presentes en >=60% de las "
                    "páginas alineadas se marcan como impresos. El mapa no "
                    "se aplica directamente al OCR, porque la escritura "
                    "repetida también puede aparecer en el consenso.",
    )
    crop_preprocess: bool = Field(
        default=True,
        description="Preprocesado de los recortes antes del OCR: localización "
                    "de la tinta (crop_to_ink) y reescalado (upscale_for_ocr). "
                    "Desactivarlo alimenta el motor con el recorte crudo, para "
                    "comparar motores/resultados (escritura a mano).",
    )
    date_ocr_fallback: bool = Field(
        default=True,
        description="Segunda pasada OCR (Tesseract restringido) para los "
                    "campos de fecha day/month/year y los campos críticos "
                    "(matricula, log_number) cuando la lectura principal de "
                    "PaddleOCR no produce un valor válido.",
    )
    date_slot_ocr: bool = Field(
        default=True,
        description="Tercera pasada para day/month/year: segmentación de la "
                    "casilla en ranuras (según los separadores verticales "
                    "impresos) y OCR por carácter con restricciones "
                    "(dígitos, abreviatura de mes). Solo actúa si la "
                    "lectura principal y el OCR de respaldo no producen "
                    "valor.",
    )
    vlm_enabled: bool = Field(
        default=True,
        description="Verificador VLM local (llama-server + modelo GGUF): "
                    "arbitra solo los casos inciertos (firmas 'unclear', "
                    "campos críticos sin resolver) y no toca las lecturas "
                    "de alta confianza. Si el modelo o el binario no están "
                    "presentes, el pipeline funciona igual que sin él.",
    )
    vlm_max_crops: int = Field(
        default=40, ge=0,
        description="Límite de recortes evaluados por el verificador VLM "
                    "en una corrida (cada consulta cuesta ~1-4 s en CPU).",
    )
    vlm_timeout: float = Field(
        default=60.0, ge=5.0,
        description="Tiempo máximo (s) por consulta al servidor VLM local.",
    )
    vlm_threads: Optional[int] = Field(
        default=None, ge=1,
        description="Hilos de CPU del servidor VLM (None = automático).",
    )
    min_match_count: int = Field(
        default=10, ge=1,
        description="Mínimo de coincidencias ORB para aceptar la alineación",
    )
    crop_padding: float = Field(
        default=0.10, ge=0.0, le=0.5,
        description="Margen al recortar cada región, relativo al tamaño "
                    "del campo (no de la página)",
    )
    confidence_warning: float = Field(
        default=0.5, ge=0.0, le=1.0,
        description="Confianza OCR bajo la cual se emite WARNING",
    )
    ocr_engine: str = "paddle"
    ocr_lang: str = "en"
    ocr_rec_model: Optional[str] = Field(
        default=None,
        description="Modelo de reconocimiento de PaddleOCR para forzar "
                    "(p. ej. PP-OCRv5_mobile_rec o PP-OCRv6_medium_rec). "
                    "None = automático: se usa el manuscrito si está "
                    "precargado en portable/paddlex, si no el general.",
    )
    ocr_det_model: Optional[str] = Field(
        default=None,
        description="Modelo de detección de PaddleOCR (None = automático: "
                    "PP-OCRv6_medium_det si está precargado en "
                    "portable/paddlex, si no PP-OCRv6_tiny_det). El "
                    "medium detecta manuscrito pequeño que el tiny no "
                    "capta.",
    )
    template_dir: Path = Field(
        default=Path("templates"),
        description="Directorio donde se buscan plantillas",
    )
    output_dir: Path = Field(default=Path("output"))
    log_dir: Path = Field(default=Path("output/logs"))

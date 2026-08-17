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
    signature_book_background: bool = Field(
        default=True,
        description="Contrastar las firmas que quedan inciertas con el resto "
                    "de la bitácora: la mediana de cada campo a lo largo del "
                    "libro es ese campo vacío, y las páginas ya resueltas "
                    "dicen cuánta tinta significa 'firmado' en este escaneo. "
                    "Solo revisa lo que quedó en duda; los veredictos firmes "
                    "no se tocan.",
    )
    signature_background_pages: int = Field(
        default=32, ge=4, le=60,
        description="Páginas del libro que se muestrean para construir ese "
                    "fondo. Cada una cuesta un renderizado. Medido sobre una "
                    "bitácora etiquetada: con 12 páginas ya se resuelve parte "
                    "de las dudas y con 32 se llega al máximo (no mejora con "
                    "40); por debajo de eso el resultado depende de si la "
                    "muestra pilla o no páginas sin firmar.",
    )
    crop_preprocess: bool = Field(
        default=True,
        description="Preprocesado de los recortes antes del OCR: localización "
                    "de la tinta (crop_to_ink) y reescalado (upscale_for_ocr). "
                    "Desactivarlo alimenta el motor con el recorte crudo, para "
                    "comparar motores/resultados (escritura a mano).",
    )
    date_ocr_fallback: bool = Field(
        default=False,
        description="Segunda pasada OCR (Tesseract restringido) para los "
                    "campos de fecha day/month/year y los campos críticos "
                    "(matricula, log_number) cuando la lectura principal de "
                    "PaddleOCR no produce un valor válido.",
    )
    date_slot_ocr: bool = Field(
        default=False,
        description="Lectura estructurada para day/month/year: segmentación de la "
                    "casilla en ranuras (según los separadores verticales "
                    "impresos) y OCR por carácter con restricciones "
                    "(dígitos, abreviatura de mes). Verifica siempre la "
                    "lectura global cuando la retícula está disponible.",
    )
    date_dynamic_geometry: bool = Field(
        default=True,
        description="Ajuste dinámico por página de las casillas de fecha: "
                    "detecta la retícula impresa (bordes y separadores) en "
                    "la ventana de la plantilla y alinea el peine esperado "
                    "con una traslación/escala pequeñas. Si la retícula no "
                    "encaja, se conserva la geometría de plantilla.",
    )
    vlm_enabled: bool = Field(
        default=False,
        description="Verificador VLM local (llama-server + modelo GGUF): "
                    "procesa todas las fechas y arbitra firmas inciertas y "
                    "campos críticos sin resolver. Si el modelo o el "
                    "binario no están presentes, el pipeline conserva el "
                    "fallback OCR.",
    )
    vlm_model: Optional[Path] = Field(
        default=None,
        description="Ruta explícita al GGUF del modelo VLM. Si es None, "
                    "se usa BITS_LLAMA_MODEL o Qwen3-VL por defecto.",
    )
    vlm_mmproj: Optional[Path] = Field(
        default=None,
        description="Ruta explícita al proyector multimodal GGUF. Si es "
                    "None, se usa BITS_LLAMA_MMPROJ o el mmproj compatible.",
    )
    vlm_max_crops: int = Field(
        default=120, ge=0,
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
    date_engine_name: str = ""
    date_dpi: int = Field(default=600, ge=72, le=600,
                          description="DPI del renderizado para regiones de fecha")
    ocr_rec_model: Optional[str] = Field(
        default="PP-OCRv5_mobile_rec",
        description="Modelo fijo de reconocimiento manuscrito de PaddleOCR.",
    )
    ocr_det_model: Optional[str] = Field(
        default="PP-OCRv6_medium_det",
        description="Modelo fijo de detección de manuscrito de PaddleOCR.",
    )
    template_dir: Path = Field(
        default=Path("templates"),
        description="Directorio donde se buscan plantillas",
    )
    output_dir: Path = Field(default=Path("output"))
    log_dir: Path = Field(default=Path("output/logs"))
    verify_fleet: bool = Field(
        default=False,
        description="Comparar las matrículas leídas contra fleet.json.",
    )
    fleet_file: Path = Field(
        default=Path("fleet.json"),
        description="Archivo portable con la lista de matrículas autorizadas.",
    )


def config_for_pdf(config: AppConfig, pdf_path: Path) -> AppConfig:
    """Ajusta resolución de trabajo y detalle al escaneo de cada PDF.

    La página completa nunca se interpola por encima de ``config.dpi``. La
    banda manuscrita sí aprovecha hasta ``date_dpi`` píxeles reales del PDF,
    pero tampoco se sobreamplía si el documento fuente tiene menos detalle.
    """
    from app.vision.pdf_loader import detect_dpi

    source_dpi = detect_dpi(Path(pdf_path), default=config.date_dpi)
    base_dpi = max(72, min(config.dpi, source_dpi))
    date_dpi = max(base_dpi, min(config.date_dpi, source_dpi))
    return config.model_copy(update={"dpi": base_dpi, "date_dpi": date_dpi})

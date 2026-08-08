"""Módulo de OCR regional."""

from app.ocr.engine import (
    OcrEngine,
    PaddleOcrEngine,
    TesseractOcrEngine,
    create_engine,
)
from app.ocr.regional import ocr_region

__all__ = [
    "OcrEngine",
    "PaddleOcrEngine",
    "TesseractOcrEngine",
    "create_engine",
    "ocr_region",
]

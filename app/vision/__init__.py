"""Módulo de visión por computador: PDF, preprocesado, alineación, firmas."""

from app.vision.alignment import align_to_template
from app.vision.blank_detection import is_blank
from app.vision.checkbox import detect_checkbox
from app.vision.marks import analyze_ink
from app.vision.pdf_loader import render_page, render_pdf_pages
from app.vision.preprocessing import (
    binarize,
    crop_region,
    denoise,
    deskew,
    rotate,
    to_gray,
    upscale_for_ocr,
)
from app.vision.signature import detect_signature

__all__ = [
    "align_to_template",
    "is_blank",
    "detect_checkbox",
    "analyze_ink",
    "render_page",
    "render_pdf_pages",
    "binarize",
    "crop_region",
    "denoise",
    "deskew",
    "rotate",
    "to_gray",
    "upscale_for_ocr",
    "detect_signature",
]

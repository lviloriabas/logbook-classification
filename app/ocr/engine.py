"""Motores OCR intercambiables (PaddleOCR + fallback Tesseract).

Arquitectura: el pipeline depende del protocolo ``OcrEngine``, de modo que
se pueden añadir nuevos motores (p. ej. Qwen VL) sin tocar el núcleo.
"""

from __future__ import annotations

import inspect
import os
from pathlib import Path
from typing import List, Optional, Protocol

import numpy as np
from loguru import logger

from app.models.schemas import OcrResult
from app.utils.io import resolve_tesseract_path
from app.utils.portable import ensure_portable_env


def _init_params(cls) -> set:
    """Parámetros aceptados por el constructor de una clase."""
    return set(inspect.signature(cls.__init__).parameters)


class OcrEngine(Protocol):
    """Interfaz común de un motor de reconocimiento de texto."""

    name: str

    def recognize(self, image: np.ndarray) -> List[OcrResult]:
        """Reconoce el texto de una imagen.

        Args:
            image: Imagen BGR.

        Returns:
            Lista de líneas de texto con confianza.
        """
        ...

    def recognize_batch(self, images: List[np.ndarray]) -> List[List[OcrResult]]:
        """Reconoce varias imágenes en una sola llamada al motor.

        Args:
            images: Imágenes BGR.

        Returns:
            Lista (una por imagen) de líneas de texto con confianza.
        """
        ...


class PaddleOcrEngine:
    """Motor OCR basado en PaddleOCR (carga perezosa).

    Por defecto usa det ``PP-OCRv6_tiny_det`` (rápido en CPU, mantiene los
    recuadros del texto manuscrito). Para el reconocimiento se elige el
    mejor modelo disponible en la caché portable (portable/paddlex/
    official_models): ``PP-OCRv5_mobile_rec`` es la generación con mejor
    rendimiento en escritura a mano (documentado por PaddleOCR) y se
    prefiere si está precargada; si no, la general ``PP-OCRv6_medium_rec``.
    Se puede forzar con ``rec_model`` (path local o nombre registrado).
    """

    name = "paddle"

    _HANDWRITTEN_REC_MODEL = "PP-OCRv5_mobile_rec"
    _DEFAULT_REC_MODEL = "PP-OCRv6_medium_rec"
    _HANDWRITTEN_DET_MODEL = "PP-OCRv6_medium_det"
    _DEFAULT_DET_MODEL = "PP-OCRv6_tiny_det"

    def __init__(self, lang: str = "en", cpu_threads: Optional[int] = None,
                 det_model: Optional[str] = None,
                 rec_model: Optional[str] = None, **kwargs) -> None:
        self.lang = lang
        self._cpu_threads = cpu_threads
        self._det_model = det_model or self._auto_det_model()
        self._rec_model = rec_model or self._auto_rec_model()
        self._extra_kwargs = kwargs
        self._engine = None

    @classmethod
    def _auto_rec_model(cls) -> str:
        """Elige el modelo de reconocimiento según la caché portable.

        Si el modelo v5 (mejor en escritura a mano) ya está precargado en
        portable/paddlex/official_models se usa automáticamente; si no, la
        general v6. Todo dentro de la carpeta del proyecto: portabilidad
        total, sin descargas en tiempo de ejecución.
        """
        root = Path(__file__).resolve().parents[2]
        cached = (
            root / "portable" / "paddlex" / "official_models"
            / cls._HANDWRITTEN_REC_MODEL
        )
        if cached.is_dir():
            return cls._HANDWRITTEN_REC_MODEL
        return cls._DEFAULT_REC_MODEL

    @classmethod
    def _auto_det_model(cls) -> str:
        """Detector: usa el medium (mejor con manuscrito pequeño) si está
        precargado; si no, el tiny (más rápido). Todo portable."""
        root = Path(__file__).resolve().parents[2]
        cached = (
            root / "portable" / "paddlex" / "official_models"
            / cls._HANDWRITTEN_DET_MODEL
        )
        if cached.is_dir():
            return cls._HANDWRITTEN_DET_MODEL
        return cls._DEFAULT_DET_MODEL

    @classmethod
    def describe_models(cls) -> str:
        """Descripción de los modelos reconocidos disponibles (log/info)."""
        root = Path(__file__).resolve().parents[2]
        det = cls._auto_det_model()
        rec = cls._auto_rec_model()
        det_ok = (root / "portable" / "paddlex" / "official_models" / det).is_dir()
        rec_ok = (root / "portable" / "paddlex" / "official_models" / rec).is_dir()
        return (f"det={det}({det_ok}) rec={rec}({rec_ok})")

    def _ensure_engine(self):
        if self._engine is not None:
            return self._engine
        # Mantiene los modelos dentro de la carpeta del proyecto (portable).
        ensure_portable_env()
        # Evita el modo oneDNN de paddlex (bug conocido en Windows):
        # ConvertPirAttribute2RuntimeAttribute not supported.
        os.environ.setdefault("PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT", "0")
        try:
            from paddleocr import PaddleOCR
        except ImportError as exc:
            raise RuntimeError(
                "PaddleOCR no está instalado. Ejecute:\n"
                "  python -m pip install --user paddlepaddle paddleocr"
            ) from exc
        logger.info("Inicializando PaddleOCR (primera carga puede tardar)...")
        if "use_angle_cls" in _init_params(PaddleOCR):
            kwargs = dict(self._extra_kwargs)
            if self._cpu_threads is not None:
                kwargs["cpu_threads"] = self._cpu_threads
            self._engine = PaddleOCR(
                use_angle_cls=True, lang=self.lang, show_log=False,
                **kwargs,
            )
        else:
            # API v3.x: use_angle_cls/show_log eliminados. Se desactivan los
            # preprocesadores de documento (orientación, desenvolvimiento y
            # orientación de líneas): recortan/transforman la imagen y
            # desplazan las coordenadas de los recortes regionales.
            defaults = {
                "use_doc_orientation_classify": False,
                "use_doc_unwarping": False,
                "use_textline_orientation": False,
            }
            kwargs = {**defaults, **self._extra_kwargs}
            kwargs["text_detection_model_name"] = self._det_model
            kwargs["text_recognition_model_name"] = self._rec_model
            if self._cpu_threads is not None:
                kwargs["cpu_threads"] = self._cpu_threads
            self._engine = PaddleOCR(lang=self.lang, **kwargs)
        return self._engine

    def recognize(self, image: np.ndarray) -> List[OcrResult]:
        engine = self._ensure_engine()
        image = self._to_bgr(image)
        try:
            raw = engine.ocr(image, cls=True)  # API v2.x
        except TypeError:
            raw = engine.predict(image)  # API v3.x
        return self._parse(raw)

    def recognize_batch(self, images: List[np.ndarray]) -> List[List[OcrResult]]:
        """Reconoce varias imágenes en una sola llamada (API v3.x).

        En API v2.x, o si la llamada en lote falla, se procesa una por una.
        """
        engine = self._ensure_engine()
        images = [self._to_bgr(img) for img in images]
        if not images:
            return []
        try:
            raw = engine.predict(images)
            parsed = [self._parse(item) for item in raw]
            if len(parsed) == len(images):
                return parsed
        except Exception:  # noqa: BLE001 - fallback robusto
            logger.debug("OCR en lote falló; se procesa imagen por imagen",
                         exc_info=True)
        return [self.recognize(img) for img in images]

    @staticmethod
    def _to_bgr(image: np.ndarray) -> np.ndarray:
        if image.ndim == 2:
            import cv2

            return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
        return image

    @staticmethod
    def _parse(raw) -> List[OcrResult]:
        """Normaliza la salida de PaddleOCR v2.x y v3.x."""
        if raw is None:
            return []

        results: List[OcrResult] = []

        # Formato v2.x: list[ list[ [box, (texto, conf)] ] ]
        try:
            for page_lines in raw:
                if page_lines is None:
                    continue
                for line in page_lines:
                    box, (text, conf) = line
                    results.append(
                        OcrResult(text=str(text), confidence=float(conf),
                                  box=box)
                    )
        except (TypeError, ValueError):
            results = []

        if results:
            return results

        # Formato v3.x: objetos OCRResult con atributos rec_texts/rec_scores
        try:
            first = raw[0] if isinstance(raw, (list, tuple)) else raw
            texts = getattr(first, "rec_texts", None) or first.get("rec_texts")
            scores = getattr(first, "rec_scores", None) or first.get("rec_scores")
            polys = getattr(first, "rec_polys", None) or first.get("rec_polys")
            if texts:
                for i, text in enumerate(texts):
                    conf = float(scores[i]) if scores and i < len(scores) else 0.0
                    box = polys[i] if polys and i < len(polys) else None
                    results.append(
                        OcrResult(text=str(text), confidence=conf, box=box)
                    )
        except (TypeError, ValueError, AttributeError):
            pass

        return results


class TesseractOcrEngine:
    """Motor OCR alternativo basado en Tesseract (útil en entornos
    corporativos donde PaddleOCR no puede instalarse)."""

    name = "tesseract"

    def __init__(self, lang: str = "en", tesseract_cmd: Optional[str] = None,
                 **kwargs):
        self.lang = lang
        if tesseract_cmd is None:
            tesseract_cmd = resolve_tesseract_path()
        self._tesseract_cmd = tesseract_cmd

    def recognize(self, image: np.ndarray,
                  config: Optional[str] = None) -> List[OcrResult]:
        try:
            import pytesseract
        except ImportError as exc:
            raise RuntimeError(
                "pytesseract no está instalado: pip install --user pytesseract"
            ) from exc
        if not self._tesseract_cmd:
            raise RuntimeError(
                "No se encontró tesseract.exe. Instálelo en "
                "portable/tesseract o añádalo al PATH."
            )
        pytesseract.pytesseract.tesseract_cmd = self._tesseract_cmd

        data = pytesseract.image_to_data(
            image, lang=self.lang, config=config or "--psm 6 --oem 3",
            output_type=pytesseract.Output.DICT,
        )
        results: List[OcrResult] = []
        for i, text in enumerate(data["text"]):
            text = str(text).strip()
            if not text:
                continue
            try:
                conf = float(data["conf"][i]) / 100.0
            except (ValueError, TypeError):
                conf = 0.0
            results.append(OcrResult(text=text, confidence=conf))
        return results

    def recognize_batch(self, images: List[np.ndarray]) -> List[List[OcrResult]]:
        return [self.recognize(img) for img in images]


def create_engine(name: str, lang: str = "en", **kwargs) -> OcrEngine:
    """Fábrica de motores OCR.

    Args:
        name: "paddle" o "tesseract".
        lang: Idioma (por defecto "en").
        kwargs: Parámetros adicionales del motor.

    Returns:
        Una instancia de OcrEngine.

    Raises:
        ValueError: Si el motor no está registrado.
    """
    engines = {
        "paddle": PaddleOcrEngine,
        "tesseract": TesseractOcrEngine,
    }
    if name not in engines:
        raise ValueError(
            f"Motor OCR desconocido: {name}. "
            f"Disponibles: {', '.join(engines)}"
        )
    logger.info(f"Creando motor OCR: {name} (lang={lang})")
    return engines[name](lang=lang, **kwargs)

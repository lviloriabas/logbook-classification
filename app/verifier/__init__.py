"""Verificador VLM local (llama-server + modelo multimodal GGUF).

El VLM local Qwen3-VL procesa las fechas y arbitra firmas o campos críticos
inciertos sobre recortes de la página. Si el binario o el modelo no están
presentes, el pipeline conserva el fallback OCR.
"""

from app.verifier.verifier import VlmVerifier

__all__ = ["VlmVerifier"]

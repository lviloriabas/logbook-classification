"""Verificador VLM local (llama-server + modelo multimodal GGUF).

Fase 1 del refuerzo de confiabilidad: cuando los detectores algorítmicos
(Fase 0) no llegan a una decisión —firmas ``unclear``, campos críticos
vacíos— un pequeño VLM local (SmolVLM2 GGUF) arbitra sobre los recortes
de los campos. El verificador es un complemento opcional: si el binario o
el modelo no están presentes, o el servidor inicia, el pipeline funciona
exactamente igual que sin él.
"""

from app.verifier.verifier import VlmVerifier

__all__ = ["VlmVerifier"]
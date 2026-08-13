"""Cliente del servidor VLM local (API compatible OpenAI).

Procesa las fechas y resuelve casos que los detectores algorítmicos (Fase 0)
no pueden cerrar: si una firma quedó "unclear" o un campo crítico quedó
vacío, se recorta la región (en la página ya alineada) y se le pregunta al
modelo multimodal local. Solo se aplican respuestas terminantes (PRESENTE /
AUSENTE, o un texto que pasa el postprocesado del campo); lo ambiguo se
descarta.

El verificador nunca lanza excepciones hacia el flujo: cualquier fallo de
servidor, timeout o presupuesto devuelve ``None`` y el pipeline conserva
su resultado previo.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from typing import Optional

import cv2
import numpy as np
from loguru import logger

from app.core.config import AppConfig
from app.verifier.launcher import LlamaServer, VlmPaths, resolve_paths

_MAX_PIXELS = 512  # lado máximo del recorte (tamaño típico del VLM)


class VlmVerifier:
    """Cliente de un ``llama-server`` local levantado a demanda.

    Attributes:
        config: app.core.config.AppConfig (vlm_enabled, vlm_max_crops,
            vlm_timeout, vlm_threads).
        available: True si el servidor quedó listo.
        crops_used: Número de recortes consultados en esta corrida.
    """

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.available = False
        self.crops_used = 0
        self._server: Optional[LlamaServer] = None
        self._started = False
        self._budget_notified = False
        self.paths: Optional[VlmPaths] = None
        self.model_name: Optional[str] = None

    # ── Ciclo de vida ───────────────────────────────────────────────────

    def ensure_server(self) -> bool:
        """Levanta el servidor (una vez) y devuelve si quedó disponible."""
        if not self.config.vlm_enabled:
            return False
        if self._started:
            return self.available
        self._started = True
        self.paths = resolve_paths(
            model=self.config.vlm_model,
            mmproj=self.config.vlm_mmproj,
        )
        if self.paths.model is not None:
            self.model_name = self.paths.model.name
        if not self.paths.complete:
            logger.info("[VLM] Sin llama-server/modelos GGUF; no se usa")
            return False
        self._server = LlamaServer(
            threads=self.config.vlm_threads,
            paths=self.paths,
        )
        self.available = self._server.start(timeout_s=180.0)
        return self.available

    def shutdown(self) -> None:
        """Detiene el servidor (entre corridas del GUI)."""
        if self._server is not None:
            self._server.stop()
        self.available = False
        self._started = False

    # ── Presupuesto ─────────────────────────────────────────────────────

    def _has_budget(self) -> bool:
        if self.crops_used < self.config.vlm_max_crops:
            return True
        if not self._budget_notified:
            self._budget_notified = True
            logger.warning(
                f"[VLM] Presupuesto agotado ({self.config.vlm_max_crops} "
                f"recortes); el resto queda sin verificar"
            )
        return False

    # ── Consulta HTTP ───────────────────────────────────────────────────

    def _ask(self, prompt: str, crop: Optional[np.ndarray]) -> Optional[str]:
        if not self.ensure_server():
            return None
        if not self._has_budget() or crop is None:
            return None
        b64 = self._encode_image(crop)
        if b64 is None:
            return None
        body = {
            "model": "local",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url",
                         "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    ],
                }
            ],
            "max_tokens": 96,
            "temperature": 0,
        }
        url = f"{self._server.base_url}/v1/chat/completions"
        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.vlm_timeout) as resp:  # noqa: E501
                data = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:  # noqa: E501
            logger.debug(f"[VLM] Consulta falló: {exc}")
            return None
        self.crops_used += 1
        try:
            text = data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError):
            return None
        return text or None

    @staticmethod
    def _encode_image(crop: np.ndarray) -> Optional[str]:
        """Recorte BGR/gris → JPEG base64 (acotado al lado máximo)."""
        try:
            if crop.ndim == 2:
                image = cv2.cvtColor(crop, cv2.COLOR_GRAY2BGR)
            else:
                image = crop
            height, width = image.shape[:2]
            if max(height, width) > _MAX_PIXELS:
                scale = _MAX_PIXELS / max(height, width)
                image = cv2.resize(image, None, fx=scale, fy=scale,
                                   interpolation=cv2.INTER_AREA)
            ok, buf = cv2.imencode(".jpg", image,
                                   [cv2.IMWRITE_JPEG_QUALITY, 90])
            if not ok:
                return None
            return base64.b64encode(buf.tobytes()).decode("ascii")
        except Exception:  # noqa: BLE001 - recorte inválido
            return None

    # ── Operaciones ─────────────────────────────────────────────────────

    def check_signature(self, crop: np.ndarray) -> Optional[bool]:
        """True/False si la casilla tiene/no tiene firma; None si ambiguo."""
        prompt = (
            "La imagen es el recorte de una casilla de FIRMA en una "
            "bitácora de vuelo. Responde sin explicaciones con una sola "
            "palabra: PRESENTE si hay una firma manuscrita real, AUSENTE "
            "si el campo está en blanco o solo tiene la línea impresa, o "
            "INCIERTO si no estás seguro."
        )
        answer = self._ask(prompt, crop)
        if not answer:
            return None
        upper = answer.strip().upper()
        if "PRESENTE" in upper and "AUSENTE" not in upper:
            return True
        if "AUSENTE" in upper:
            return False
        return None

    def read_text(self, crop: np.ndarray, kind: str) -> Optional[str]:
        """Lee el valor de un campo crítico; None si ilegible.

        Args:
            crop: Recorte del campo.
            kind: "matricula" | "digits" | "day" | "month" | "year".
        """
        prompts = {
            "matricula": (
                "Esta imagen es el recorte del campo MATRICULA de una "
                "bitacora. Escribe UNICAMENTE la matricula tal como "
                "aparece, por ejemplo HP-1234CMP. Si no puedes leerla "
                "con seguridad responde NO LEGIBLE."
            ),
            "digits": (
                "Esta imagen es el recorte de un campo NUMERICO de una "
                "bitacora (numero de log). Escribe solo los digitos, sin "
                "separadores ni comentarios. Si no puedes leerlo responde "
                "NO LEGIBLE."
            ),
            "day": (
                "Esta imagen es el recorte del campo DAY de la fecha en "
                "una bitacora de mantenimiento de aeronave. La casilla "
                "tiene dos posiciones y puede contener lineas verticales "
                "impresas entre los digitos. Ignora las lineas, bordes y "
                "rotulos impresos. Escribe UNICAMENTE el dia manuscrito, "
                "con uno o dos digitos, por ejemplo 7 o 20. No inventes "
                "un digito si no se ve; en ese caso responde NO LEGIBLE."
            ),
            "month": (
                "Esta imagen es el recorte del campo MES de una bitacora "
                "(abreviatura del mes en la fecha). Escribe UNICAMENTE la "
                "abreviatura de 3 letras, por ejemplo JUL. Si no puedes "
                "leerla con seguridad responde NO LEGIBLE."
            ),
            "year": (
                "Esta imagen es el recorte del campo YR de la fecha en una "
                "bitacora de mantenimiento de aeronave. Puede contener dos "
                "posiciones y una linea vertical impresa entre los digitos. "
                "Ignora las lineas, bordes y rotulos impresos. Escribe "
                "UNICAMENTE el año manuscrito como dos o cuatro digitos, "
                "por ejemplo 26 o 2026. Si no puedes leerlo con seguridad "
                "responde NO LEGIBLE."
            ),
        }
        prompt = prompts.get(kind)
        if prompt is None:
            return None
        answer = self._ask(prompt, crop)
        if not answer:
            return None
        upper = answer.strip().upper()
        if "NO LEGIBLE" in upper or "NO SE PUEDE" in upper:
            return None
        token = answer.strip().replace("\n", " ")
        return token if token else None

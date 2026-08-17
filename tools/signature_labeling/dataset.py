"""Conjunto de recortes de firma etiquetados a mano (suite temporal).

Aquí vive todo lo que comparten las tres herramientas de la suite: dónde se
guardan los recortes, cómo se nombran, qué etiquetas existen y —lo más
importante— cómo se reproduce un recorte con un margen distinto sin volver a
abrir el PDF.

El recorte se guarda con un margen generoso (``EXTRACT_PAD_*``) y con el
rectángulo exacto del campo dentro de la imagen. Con esas dos cosas el
calibrador puede simular cualquier margen menor o igual recortando el PNG, que
es lo que permite tratar el margen como un parámetro más de la búsqueda en
lugar de tener que volver a extraer el lote entero por cada valor.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field as dc_field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

# Etiquetas humanas. "dudosa" es para el recorte que ni una persona puede
# resolver (escaneo quemado, la firma del vecino invade el campo): queda fuera
# de las métricas en lugar de contaminarlas con una respuesta inventada.
LABEL_PRESENT = "firma"
LABEL_ABSENT = "ausente"
LABEL_UNSURE = "dudosa"
LABEL_ORDER: Tuple[str, ...] = (LABEL_PRESENT, LABEL_ABSENT, LABEL_UNSURE)

# Veredicto del detector que corresponde a cada etiqueta.
EXPECTED_VALUE = {LABEL_PRESENT: "true", LABEL_ABSENT: "false"}

MANIFEST_NAME = "manifest.json"
LABELS_NAME = "labels.json"
CROPS_DIR = "recortes"

# Margen con el que se guardan los recortes, relativo al tamaño del campo (la
# misma unidad que ``crop_region``). Es más ancho que el del detector para que
# quien etiqueta vea el contexto y para que el calibrador pueda estrecharlo.
EXTRACT_PAD_X = 0.25
EXTRACT_PAD_Y = 0.35


def pad_pixels(pad: float, size: int) -> int:
    """Margen en píxeles, con la misma fórmula que ``crop_region``.

    El mínimo de 1 px no es un detalle: el detector usa ``pad_x = 0.0`` y aun
    así recorta un píxel a cada lado. Reproducirlo es lo que hace que el
    recorte simulado sea idéntico al que ve el pipeline.
    """
    return max(1, round(pad * size))


@dataclass
class Sample:
    """Un recorte de campo de firma extraído de una página."""

    id: str
    pdf: str
    page: int
    field_id: str
    file: str
    dpi: int
    alignment: str  # "ok" | "low" | "sin_alinear"
    rect: List[int]  # [x0, y0, x1, y1] del campo dentro del recorte guardado

    @property
    def field_width(self) -> int:
        return self.rect[2] - self.rect[0]

    @property
    def field_height(self) -> int:
        return self.rect[3] - self.rect[1]


@dataclass
class Dataset:
    """Manifiesto + etiquetas de una carpeta de trabajo."""

    root: Path
    samples: List[Sample] = dc_field(default_factory=list)
    labels: Dict[str, str] = dc_field(default_factory=dict)
    created: str = ""

    # -- E/S ---------------------------------------------------------------

    @classmethod
    def load(cls, root: Path) -> "Dataset":
        root = Path(root)
        manifest_path = root / MANIFEST_NAME
        if not manifest_path.is_file():
            raise FileNotFoundError(
                f"No hay manifiesto en {root}. Ejecute primero extract.py."
            )
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        samples = [Sample(**item) for item in payload["samples"]]
        labels: Dict[str, str] = {}
        labels_path = root / LABELS_NAME
        if labels_path.is_file():
            stored = json.loads(labels_path.read_text(encoding="utf-8"))
            labels = {
                key: value for key, value in stored.get("labels", {}).items()
                if value in LABEL_ORDER
            }
        return cls(
            root=root,
            samples=samples,
            labels=labels,
            created=payload.get("created", ""),
        )

    def save_manifest(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "created": self.created or datetime.now().isoformat(timespec="seconds"),
            "extract_pad_x": EXTRACT_PAD_X,
            "extract_pad_y": EXTRACT_PAD_Y,
            "samples": [asdict(sample) for sample in self.samples],
        }
        (self.root / MANIFEST_NAME).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def save_labels(self) -> None:
        """Guarda las etiquetas de forma atómica.

        Etiquetar es trabajo humano que no se puede repetir: la escritura pasa
        por un temporal y un ``replace`` para que un corte a mitad no deje el
        archivo truncado.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "updated": datetime.now().isoformat(timespec="seconds"),
            "counts": self.counts(),
            "labels": self.labels,
        }
        target = self.root / LABELS_NAME
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(target)

    # -- Consultas ---------------------------------------------------------

    def counts(self) -> Dict[str, int]:
        counts = {label: 0 for label in LABEL_ORDER}
        for value in self.labels.values():
            if value in counts:
                counts[value] += 1
        counts["total"] = len(self.samples)
        counts["etiquetadas"] = sum(counts[label] for label in LABEL_ORDER)
        return counts

    def crop_path(self, sample: Sample) -> Path:
        return self.root / sample.file

    def load_crop(self, sample: Sample) -> Optional[np.ndarray]:
        """Recorte completo tal como se guardó (con el margen generoso)."""
        image = cv2.imread(str(self.crop_path(sample)), cv2.IMREAD_COLOR)
        return image

    def load_crop_padded(
        self, sample: Sample, pad_x: float, pad_y: float
    ) -> Optional[np.ndarray]:
        """Recorte con el margen ``pad_x``/``pad_y`` que usaría el detector."""
        image = self.load_crop(sample)
        if image is None:
            return None
        return crop_with_pad(image, sample.rect, pad_x, pad_y)

    def labeled(
        self, include_unsure: bool = False
    ) -> List[Tuple[Sample, str]]:
        """Muestras con etiqueta, en el orden del manifiesto."""
        wanted = set(LABEL_ORDER) if include_unsure else {
            LABEL_PRESENT, LABEL_ABSENT
        }
        return [
            (sample, self.labels[sample.id])
            for sample in self.samples
            if self.labels.get(sample.id) in wanted
        ]


def crop_with_pad(
    image: np.ndarray, rect: Sequence[int], pad_x: float, pad_y: float
) -> np.ndarray:
    """Estrecha un recorte guardado al margen pedido.

    ``rect`` es el rectángulo del campo dentro de ``image``. El resultado es
    el mismo píxel a píxel que habría devuelto ``crop_region`` sobre la página
    con esos márgenes, siempre que no toque el borde de la página.
    """
    x0, y0, x1, y1 = (int(value) for value in rect)
    px = pad_pixels(pad_x, x1 - x0)
    py = pad_pixels(pad_y, y1 - y0)
    left = max(0, x0 - px)
    top = max(0, y0 - py)
    right = min(image.shape[1], x1 + px)
    bottom = min(image.shape[0], y1 + py)
    if right <= left or bottom <= top:
        return image
    return image[top:bottom, left:right]


def sample_id(pdf_name: str, page: int, field_id: str) -> str:
    return f"{Path(pdf_name).stem}_p{page:04d}_{field_id}"

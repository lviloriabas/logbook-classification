"""La vista previa reconstruye la página desde la geometría, no desde caché.

El preprocesado emite unos pocos flotantes por página en vez de un ``QImage``
de página completa (11.2 MB a 200 DPI, sin tope: 4.3 GB en un libro de 393
páginas). Estas pruebas fijan las dos condiciones que hacen válido el cambio:
que la geometría viaje normalizada y que los recuadros de campo sigan cayendo
sobre la misma tinta.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.gui.main_window import MainWindow
from app.models.schemas import PageResult
from app.vision.alignment import TransformResult, apply_transform


def _window() -> MainWindow:
    QApplication.instance() or QApplication([])
    return MainWindow()


def test_the_preprocessing_stores_geometry_instead_of_the_page_image():
    """Lo que se guarda por página debe ser numérico, no una imagen."""
    window = _window()
    try:
        geometry = {
            "skew_angle": 0.75,
            "alignment": {
                "rot": 0.5, "tx_ratio": 0.01, "ty_ratio": -0.02, "scale": 1.01,
            },
        }
        window._on_preprocessed_page("libro.pdf", 7, geometry)
        assert window._preprocess_geometry[("libro.pdf", 7)] == geometry
        assert not hasattr(window, "_preprocessed_images")
    finally:
        window.close()


def test_the_preview_asks_for_the_page_with_the_stored_geometry(tmp_path):
    """Sin resultado OCR manda el preprocesado; con resultado, manda el OCR.

    El resultado procesado refleja el anclaje por batch que usó el OCR, así
    que debe ganarle a la geometría medida antes por el preprocesado.
    """
    pdf = tmp_path / "libro.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    window = _window()
    asked: list[tuple] = []

    class _Requested:
        @staticmethod
        def emit(*args) -> None:
            asked.append(args)

    class _Loader:
        requested = _Requested()

    # El loader real vive en otra QThread: su señal se entrega encolada y no
    # llegaría dentro de la prueba. Solo interesa con qué se le pide la página.
    window._preview_loader = _Loader()
    try:
        window._preview_total = 3
        preprocessed = {
            "skew_angle": 1.25,
            "alignment": {
                "rot": 0.4, "tx_ratio": 0.02, "ty_ratio": 0.03, "scale": 1.0,
            },
        }
        window._preprocess_geometry[(str(pdf), 2)] = preprocessed
        window._preprocessed_active = True

        window._show_preview_page(2, pdf)
        assert asked[-1][2] == preprocessed

        page = PageResult(page_number=2)
        page.skew_angle = 3.5
        page.preview_alignment = {
            "rot": 0.9, "tx_ratio": 0.05, "ty_ratio": 0.06, "scale": 1.02,
        }
        window._preview_results[(str(Path(pdf).resolve()), 2)] = page

        window._show_preview_page(2, pdf)
        assert asked[-1][2]["skew_angle"] == 3.5
        assert asked[-1][2]["alignment"] == page.preview_alignment
    finally:
        window.close()


def test_the_stored_geometry_is_resolution_independent():
    """Los ratios deben reproducir la alineación a cualquier resolución.

    El preprocesado mide sobre la página a ``config.dpi`` y el visor
    rasteriza a 150: si la traslación viajara en píxeles, la vista previa
    quedaría desplazada y los recuadros no caerían sobre la tinta.
    """
    source = np.full((400, 300, 3), 255, dtype=np.uint8)
    source[120:200, 90:210] = 0

    transform = TransformResult(rot=1.5, tx=9.0, ty=-6.0, scale=1.0)
    height, width = source.shape[:2]
    alignment = {
        "rot": float(transform.rot),
        "tx_ratio": float(transform.tx) / width,
        "ty_ratio": float(transform.ty) / height,
        "scale": float(transform.scale),
    }
    expected = apply_transform(source, transform)

    # El mismo contenido a otra escala, como lo ve el visor.
    scale = 0.5
    smaller = np.full((200, 150, 3), 255, dtype=np.uint8)
    smaller[60:100, 45:105] = 0
    small_height, small_width = smaller.shape[:2]
    rebuilt = apply_transform(
        smaller,
        TransformResult(
            rot=alignment["rot"],
            tx=alignment["tx_ratio"] * small_width,
            ty=alignment["ty_ratio"] * small_height,
            scale=alignment["scale"],
        ),
    )

    # El centro de masa de la tinta debe coincidir en coordenadas relativas.
    def ink_center(image: np.ndarray) -> tuple[float, float]:
        dark = np.argwhere(image[:, :, 0] < 128)
        rows, columns = dark[:, 0], dark[:, 1]
        return (
            float(columns.mean()) / image.shape[1],
            float(rows.mean()) / image.shape[0],
        )

    expected_center = ink_center(expected)
    rebuilt_center = ink_center(rebuilt)
    assert abs(expected_center[0] - rebuilt_center[0]) < 0.01
    assert abs(expected_center[1] - rebuilt_center[1]) < 0.01

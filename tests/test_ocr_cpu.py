"""El motor OCR debe inicializarse siempre para ejecución en CPU."""

from __future__ import annotations

import sys
from types import ModuleType

from app.ocr.engine import PaddleOcrEngine


def _install_fake_paddleocr(monkeypatch, paddle_class) -> None:
    module = ModuleType("paddleocr")
    module.PaddleOCR = paddle_class
    monkeypatch.setitem(sys.modules, "paddleocr", module)


def test_paddle_v3_forces_cpu_even_if_caller_requests_other_device(monkeypatch):
    calls = []

    class FakePaddleOCR:
        def __init__(self, **kwargs):
            calls.append(kwargs)

    _install_fake_paddleocr(monkeypatch, FakePaddleOCR)
    PaddleOcrEngine(device="gpu")._ensure_engine()

    assert calls[0]["device"] == "cpu"


def test_paddle_v2_disables_gpu_even_if_caller_requests_it(monkeypatch):
    calls = []

    class FakePaddleOCR:
        def __init__(
            self, use_angle_cls=False, use_gpu=True, lang="en", show_log=True,
            **kwargs,
        ):
            calls.append({"use_gpu": use_gpu, **kwargs})

    _install_fake_paddleocr(monkeypatch, FakePaddleOCR)
    PaddleOcrEngine(use_gpu=True)._ensure_engine()

    assert calls[0]["use_gpu"] is False

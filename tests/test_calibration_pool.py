"""La calibracion repartida entre los procesos del pool.

Calibrar corria en el proceso principal, pagina a pagina, mientras los
procesos del pool -ya arrancados- esperaban. Estas pruebas fijan que al
repartirla el resultado no cambia: las mismas transformaciones y los mismos
angulos, en el mismo orden de pagina.
"""

from __future__ import annotations

import pickle
import unittest
from concurrent.futures import Future
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pymupdf as fitz

from app.core import pipeline as pipeline_module
from app.core.config import AppConfig
from app.core.pipeline import Pipeline
from app.templates.schema import FieldTemplate, FieldType, Template


class FakeEngine:
    name = "fake"


def _pdf(path: Path, pages: int) -> Path:
    document = fitz.open()
    for number in range(1, pages + 1):
        page = document.new_page(width=200, height=280)
        page.insert_text((40, 60), f"PAGINA {number}", fontsize=16)
        page.draw_rect(fitz.Rect(20, 20, 180, 260))
    document.save(str(path))
    document.close()
    return path


class _InlineExecutor:
    """Ejecuta cada envio en el acto, en este mismo proceso."""

    def __init__(self):
        self.submitted = []

    def submit(self, fn, *args):
        self.submitted.append(args)
        future: Future = Future()
        try:
            future.set_result(fn(*args))
        except BaseException as exc:  # noqa: BLE001 - se reexpone al llamador
            future.set_exception(exc)
        return future


class _FakePool:
    """Pool con la misma superficie que usa la calibracion."""

    def __init__(self, tmp_path: Path):
        self.max_workers = 4
        self._tmp = tmp_path
        self.executor = _InlineExecutor()
        self.released = []
        self._index = 0

    def prepare(self, state: dict) -> Path:
        self._index += 1
        path = self._tmp / f"calib_{self._index}.pickle"
        with path.open("wb") as stream:
            pickle.dump(state, stream, protocol=pickle.HIGHEST_PROTOCOL)
        return path

    def release(self, path: Path) -> None:
        self.released.append(path)
        path.unlink(missing_ok=True)


class TestPooledCalibrationMatchesSerial(unittest.TestCase):
    def setUp(self):
        import tempfile

        self._dir = tempfile.TemporaryDirectory(prefix="bits_calib_test_")
        self.tmp = Path(self._dir.name)
        self.pdf = _pdf(self.tmp / "libro.pdf", 9)
        self.config = AppConfig(dpi=150, align=True, deskew=True,
                                remove_printed=False, date_slot_ocr=False)
        self.template = Template(name="fixture")
        pipeline_module._WORKER_STATE.clear()

    def tearDown(self):
        pipeline_module._WORKER_STATE.clear()
        self._dir.cleanup()

    def _reference(self) -> np.ndarray:
        from app.vision.pdf_loader import render_page

        return render_page(self.pdf, 1, self.config.dpi)

    def test_same_transforms_and_angles_as_the_serial_route(self):
        reference = self._reference()

        serial = Pipeline(self.config, FakeEngine(), self.template)
        own_s, anchors_s = serial._calibrate_impl(self.pdf, 1, 9, reference)

        pool = _FakePool(self.tmp)
        pooled = Pipeline(self.config, FakeEngine(), self.template,
                          workers=4, process_pool=pool)
        own_p, anchors_p = pooled._calibrate_impl(self.pdf, 1, 9, reference)

        self.assertEqual(len(own_p), len(own_s))
        for left, right in zip(own_s, own_p):
            self.assertEqual(
                (left.rot, left.tx, left.ty, left.scale, left.reliable),
                (right.rot, right.tx, right.ty, right.scale, right.reliable),
            )
        for left, right in zip(anchors_s, anchors_p):
            self.assertEqual(
                (left.rot, left.tx, left.ty, left.scale, left.reliable),
                (right.rot, right.tx, right.ty, right.scale, right.reliable),
            )
        self.assertEqual(pooled._skew_angles, serial._skew_angles)

    def test_pages_are_calibrated_in_order_and_the_state_is_released(self):
        pool = _FakePool(self.tmp)
        pooled = Pipeline(self.config, FakeEngine(), self.template,
                          workers=4, process_pool=pool)
        pooled._calibrate_impl(self.pdf, 3, 4, self._reference())

        self.assertEqual([args[0] for args in pool.executor.submitted],
                         [3, 4, 5, 6])
        self.assertEqual(len(pool.released), 1)
        self.assertFalse(pool.released[0].exists())

    def test_a_pool_failure_falls_back_to_the_serial_route(self):
        pool = _FakePool(self.tmp)
        pooled = Pipeline(self.config, FakeEngine(), self.template,
                          workers=4, process_pool=pool)

        with patch.object(pipeline_module, "_calibrate_page_worker",
                          side_effect=RuntimeError("pool caido")):
            own, anchors = pooled._calibrate_impl(self.pdf, 1, 9,
                                                  self._reference())

        self.assertEqual(len(own), 9)
        self.assertEqual(len(anchors), 9)
        self.assertEqual(len(pooled._skew_angles), 9)

    def test_the_background_consensus_keeps_the_serial_route(self):
        """Ese consenso necesita la pagina en grises en el proceso padre."""
        template = Template(name="con casillas", fields=[
            FieldTemplate(id="marca", type=FieldType.CHECKBOX,
                          x=0.1, y=0.1, w=0.2, h=0.2),
        ])
        pool = _FakePool(self.tmp)
        pooled = Pipeline(
            self.config.model_copy(update={"remove_printed": True}),
            FakeEngine(), template, workers=4, process_pool=pool,
        )
        pooled._calibrate_impl(self.pdf, 1, 9, self._reference())

        self.assertEqual(pool.executor.submitted, [])

    def test_without_a_pool_it_calibrates_in_series(self):
        alone = Pipeline(self.config, FakeEngine(), self.template, workers=4)
        own, _anchors = alone._calibrate_impl(self.pdf, 1, 9,
                                              self._reference())
        self.assertEqual(len(own), 9)


if __name__ == "__main__":
    unittest.main()

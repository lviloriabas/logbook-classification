"""La ventana no reabre un PDF para contar paginas que ya conto.

Contar paginas abre el documento, y en el hilo de la interfaz eso se nota
justo al terminar una ejecución o al cambiar de bitacora en la vista previa.
La deteccion de DPI ya recorre la entrada contandolas.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf as fitz

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.gui import main_window as main_window_module
from app.gui.main_window import MainWindow


def _pdf(path: Path, pages: int) -> Path:
    document = fitz.open()
    for _ in range(pages):
        document.new_page(width=200, height=280)
    document.save(str(path))
    document.close()
    return path


class TestPreviewPageCountReuse(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        import tempfile

        self._dir = tempfile.TemporaryDirectory(prefix="bits_preview_")
        self.tmp = Path(self._dir.name)
        self.window = MainWindow()
        self.addCleanup(self._close)

    def _close(self):
        self.window._teardown()
        self.window.deleteLater()
        self._dir.cleanup()

    def test_known_count_comes_from_the_input_scan(self):
        pdf = _pdf(self.tmp / "libro.pdf", 7)
        self.window._pdf_paths = [pdf]
        self.window._input_page_counts = [7]

        self.assertEqual(self.window._known_page_count(pdf), 7)

    def test_unknown_document_reports_nothing(self):
        pdf = _pdf(self.tmp / "otro.pdf", 3)
        self.window._pdf_paths = []
        self.window._input_page_counts = []

        self.assertIsNone(self.window._known_page_count(pdf))

    def test_changing_document_does_not_reopen_a_counted_pdf(self):
        pdf = _pdf(self.tmp / "libro.pdf", 7)
        self.window._pdf_paths = [pdf]
        self.window._input_page_counts = [7]
        self.window._preview_pdf = None

        with patch("app.vision.pdf_loader.page_count") as counted:
            self.window._show_preview_page(1, pdf)

        counted.assert_not_called()
        self.assertEqual(self.window._preview_total, 7)

    def test_an_uncounted_document_is_still_opened(self):
        pdf = _pdf(self.tmp / "suelto.pdf", 4)
        self.window._pdf_paths = []
        self.window._input_page_counts = []
        self.window._preview_pdf = None

        self.window._show_preview_page(1, pdf)

        self.assertEqual(self.window._preview_total, 4)

    def test_finishing_a_run_reuses_the_counts(self):
        pdf = _pdf(self.tmp / "libro.pdf", 7)
        self.window._pdf_paths = [pdf]
        self.window._input_page_counts = [7]
        processed = [pdf]

        with patch.object(main_window_module.MainWindow,
                          "_set_preview_documents") as published:
            counts = [self.window._known_page_count(path)
                      for path in processed]
            published(processed, counts)

        self.assertEqual(counts, [7])


class TestPreviewResizeIsDebounced(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MainWindow()
        self.addCleanup(self._close)

    def _close(self):
        self.window._teardown()
        self.window.deleteLater()

    def test_many_resizes_schedule_a_single_repaint(self):
        from PySide6.QtCore import QSize
        from PySide6.QtGui import QPixmap, QResizeEvent

        self.window._preview_source_pixmap = QPixmap(10, 10)
        with patch.object(self.window, "_render_preview_pixmap") as repaint:
            for width in range(900, 940):
                self.window.resizeEvent(
                    QResizeEvent(QSize(width, 700), QSize(width - 1, 700))
                )
            # Ningún repintado durante el arrastre, solo uno programado.
            repaint.assert_not_called()
            self.assertTrue(self.window._resize_preview_timer.isActive())
            self.assertTrue(self.window._resize_preview_timer.isSingleShot())

            # Al detenerse el arrastre se repinta una sola vez.
            self.window._resize_preview_timer.timeout.emit()
            self.assertEqual(repaint.call_count, 1)

    def test_without_an_image_nothing_is_scheduled(self):
        from PySide6.QtCore import QSize
        from PySide6.QtGui import QResizeEvent

        self.window._preview_source_pixmap = None
        self.window.resizeEvent(
            QResizeEvent(QSize(900, 700), QSize(880, 700))
        )

        self.assertFalse(self.window._resize_preview_timer.isActive())


if __name__ == "__main__":
    unittest.main()

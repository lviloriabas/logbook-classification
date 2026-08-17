"""Cierre ordenado y coste de refrescar la ventana principal."""

from __future__ import annotations

import os
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication

from app.gui.main_window import _TABLE_CELL_CHUNK, MainWindow


class _InterruptibleWorker(QThread):
    """Worker que respeta la petición de parada, como los del pipeline."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.stopped_on_request = False

    def run(self) -> None:
        for _ in range(500):
            if self.isInterruptionRequested():
                self.stopped_on_request = True
                return
            self.msleep(10)


def _until(app, condition, timeout: float = 10.0) -> bool:
    """Deja correr la cola de eventos hasta que se cumpla ``condition``."""
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        QThread.msleep(5)
    app.processEvents()
    return condition()


def test_closing_with_work_in_flight_stops_it_instead_of_destroying_the_thread():
    """Cerrar durante una corrida mataba el programa (0xC0000409).

    Qt aborta el proceso si se destruye un ``QThread`` en marcha, y el cierre
    no detenía ni el OCR, ni el preprocesado, ni la generación de salidas.
    Ahora el cierre pide la parada y solo se completa cuando ya no queda
    ningún hilo corriendo.
    """
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    worker = _InterruptibleWorker(window)
    window._outputs_worker = worker
    worker.start()
    assert _until(app, worker.isRunning)

    window.show()
    window.close()

    # El cierre se aplaza: la ventana sigue viva mientras el hilo termina.
    assert window.isVisible()
    assert window._closing

    assert _until(app, lambda: not worker.isRunning())
    # El hilo salió por la petición de parada, no porque agotara su trabajo.
    assert worker.stopped_on_request
    assert _until(app, lambda: not window.isVisible())
    assert not window._running_workers()


def test_closing_without_work_in_flight_closes_straight_away():
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.show()

    assert window.close()

    assert not window.isVisible()
    assert not window._closing
    app.processEvents()


def test_a_closing_window_does_not_queue_another_export():
    """Una exportación en cola dejaría el cierre sin terminar nunca."""
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window._reports = []
    window.btn_process.setEnabled(False)
    window.btn_preprocess.setEnabled(False)
    window._closing = True
    window._pending_export = True
    window._pending_csv_refresh = True

    window._on_outputs_thread_finished()

    assert not window._pending_export
    assert not window._pending_csv_refresh
    # Los botones siguen bloqueados: reactivarlos a medio cerrar invitaría a
    # lanzar trabajo nuevo justo cuando la ventana se está yendo.
    assert not window.btn_process.isEnabled()
    assert not window.btn_preprocess.isEnabled()
    window.close()
    app.processEvents()


def test_the_table_chunk_is_a_budget_of_cells_not_of_rows():
    """Con presupuesto en filas, un CSV ancho bloqueaba medio segundo por tramo.

    El costo de llenar la tabla es por celda, así que 400 filas cuestan lo
    mismo que 34.000 celdas cuando la corrida trae 85 columnas.
    """
    app = QApplication.instance() or QApplication([])
    window = MainWindow()

    window._table_columns = ["a", "b", "c"]
    narrow = window._rows_per_chunk()
    window._table_columns = [f"c{index}" for index in range(85)]
    wide = window._rows_per_chunk()

    assert narrow * 3 <= _TABLE_CELL_CHUNK
    assert wide * 85 <= _TABLE_CELL_CHUNK
    # Cuantas más columnas, menos filas por tramo: el tiempo de cada uno es el
    # mismo tenga la corrida tres columnas o noventa.
    assert narrow > wide >= 1
    window.close()
    app.processEvents()


def test_the_preview_reuses_the_page_counts_of_the_dpi_pass(
    tmp_path: Path, monkeypatch
):
    """Contar las páginas otra vez abría el lote entero por segunda vez."""
    app = QApplication.instance() or QApplication([])
    from app.vision import pdf_loader

    opened: list[Path] = []
    monkeypatch.setattr(
        pdf_loader, "page_count", lambda path: opened.append(Path(path)) or 5
    )
    window = MainWindow()
    pdf = tmp_path / "libro.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")

    window._set_preview_documents([pdf], [393])

    assert window._preview_document_counts == [393]
    assert opened == []

    # Sin recuento previo sí se cuenta, que es lo que hace el visor de una
    # corrida ya procesada.
    window._set_preview_documents([pdf])

    assert window._preview_document_counts == [5]
    assert opened == [pdf]
    window.close()
    app.processEvents()

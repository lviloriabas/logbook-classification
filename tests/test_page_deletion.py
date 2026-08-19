"""Supr quita de la corrida las páginas seleccionadas en el visor.

Una bitácora que no debía entrar —una hoja repetida, una página en blanco
que el escáner metió de más— se quita de la corrida sin repetir el OCR: se
reescriben el CSV, el CSV completo, el JSON y las estadísticas sin ella. Los
PDF ya entregados no se tocan aquí; se rehacen al exportar.
"""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

from app.gui.csv_viewer import CsvViewerWindow

from test_csv_viewer import _run_with_companion_json


INPUT = Path(__file__).resolve().parents[1] / "input"


def _pages_in_csv(csv_path: Path) -> list[str]:
    with csv_path.open("r", newline="", encoding="utf-8-sig") as handle:
        return [row["page"] for row in csv.DictReader(handle)]


def _pages_in_json(csv_path: Path) -> list[int]:
    payload = json.loads(
        csv_path.with_suffix(".json").read_text(encoding="utf-8")
    )
    return [
        page["page_number"]
        for report in payload.get("reportes", [])
        for page in report.get("pages", [])
    ]


def _viewer_on_run(tmp_path: Path) -> tuple[CsvViewerWindow, Path, Path]:
    run, csv_path = _run_with_companion_json(tmp_path, INPUT / "test.pdf")
    viewer = CsvViewerWindow(tmp_path)
    assert viewer.load_csv_file(csv_path)
    return viewer, run, csv_path


def _wait_for_outputs(viewer: CsvViewerWindow, app: QApplication) -> None:
    worker = viewer._outputs_worker
    assert worker is not None, "no se lanzó la escritura de la corrida"
    assert worker.wait(120000)
    app.processEvents()
    app.processEvents()


def test_supr_borra_de_la_corrida_las_paginas_seleccionadas(tmp_path: Path):
    app = QApplication.instance() or QApplication([])
    viewer, _run, csv_path = _viewer_on_run(tmp_path)
    try:
        assert _pages_in_csv(csv_path) == ["1", "2"]
        viewer.table.selectRow(0)

        with patch(
            "app.gui.csv_viewer.QMessageBox.warning",
            return_value=QMessageBox.StandardButton.Yes,
        ) as confirm:
            viewer._delete_selected_pages()
        _wait_for_outputs(viewer, app)

        # Se avisa de lo que se va a perder antes de tocar nada.
        assert "1 página(s)" in confirm.call_args.args[2]
        assert _pages_in_csv(csv_path) == ["2"]
        assert _pages_in_json(csv_path) == [2]
        # El JSON no puede seguir diciendo que la corrida tiene dos páginas.
        payload = json.loads(
            csv_path.with_suffix(".json").read_text(encoding="utf-8")
        )
        assert payload["reportes"][0]["summary"]["total_pages"] == 1
        # Y la tabla se recarga con lo que quedó.
        assert viewer.table.rowCount() == 1
        assert "eliminadas" in viewer.status_label.text()
    finally:
        viewer.pdf_viewer.shutdown()
        viewer.close()
        app.processEvents()


def test_cancelar_la_confirmacion_no_borra_nada(tmp_path: Path):
    app = QApplication.instance() or QApplication([])
    viewer, _run, csv_path = _viewer_on_run(tmp_path)
    try:
        viewer.table.selectRow(0)

        with patch(
            "app.gui.csv_viewer.QMessageBox.warning",
            return_value=QMessageBox.StandardButton.No,
        ):
            viewer._delete_selected_pages()

        assert viewer._outputs_worker is None
        assert _pages_in_csv(csv_path) == ["1", "2"]
    finally:
        viewer.pdf_viewer.shutdown()
        viewer.close()
        app.processEvents()


def test_no_se_puede_dejar_la_corrida_sin_ninguna_pagina(tmp_path: Path):
    app = QApplication.instance() or QApplication([])
    viewer, _run, csv_path = _viewer_on_run(tmp_path)
    try:
        viewer.table.selectAll()

        with patch("app.gui.csv_viewer.QMessageBox.information") as aviso, patch(
            "app.gui.csv_viewer.QMessageBox.warning",
            return_value=QMessageBox.StandardButton.Yes,
        ) as confirm:
            viewer._delete_selected_pages()

        assert aviso.called and not confirm.called
        assert viewer._outputs_worker is None
        assert _pages_in_csv(csv_path) == ["1", "2"]
    finally:
        viewer.pdf_viewer.shutdown()
        viewer.close()
        app.processEvents()


def test_sin_seleccion_solo_lo_dice(tmp_path: Path):
    app = QApplication.instance() or QApplication([])
    viewer, _run, csv_path = _viewer_on_run(tmp_path)
    try:
        viewer.table.clearSelection()

        with patch("app.gui.csv_viewer.QMessageBox.warning") as confirm:
            viewer._delete_selected_pages()

        assert not confirm.called
        assert viewer._outputs_worker is None
        assert "seleccione" in viewer.status_label.text().casefold()
        assert _pages_in_csv(csv_path) == ["1", "2"]
    finally:
        viewer.pdf_viewer.shutdown()
        viewer.close()
        app.processEvents()


def test_un_csv_suelto_no_es_una_corrida_de_la_que_borrar(tmp_path: Path):
    app = QApplication.instance() or QApplication([])
    viewer = CsvViewerWindow(tmp_path)
    suelto = tmp_path / "suelto.csv"
    suelto.write_text("file,page\na.pdf,1\n", encoding="utf-8")
    try:
        assert viewer.load_csv_file(suelto)
        viewer.table.selectRow(0)

        with patch("app.gui.csv_viewer.QMessageBox.information") as aviso:
            viewer._delete_selected_pages()

        assert aviso.called
        assert viewer._outputs_worker is None
        assert suelto.read_text(encoding="utf-8") == "file,page\na.pdf,1\n"
    finally:
        viewer.pdf_viewer.shutdown()
        viewer.close()
        app.processEvents()

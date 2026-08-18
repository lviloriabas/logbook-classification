"""El historial del visor y las casillas con las que arranca una corrida.

El visor abre una corrida sin tener que buscarla a mano: lista las últimas y
carga la elegida. Y el cuadro «Salidas» arranca marcado como se entrega
habitualmente —un solo PDF separado por matrícula, con las posibles
discrepancias al final— para no tener que marcarlo en cada corrida.
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.gui.csv_utils import find_run_dirs
from app.gui.csv_viewer import CsvViewerWindow
from app.gui.export_options import ExportOptionsGroup


def _run(root: Path, name: str, mtime: float) -> Path:
    run = root / name
    datos = run / "datos"
    datos.mkdir(parents=True)
    (datos / f"{name}.CSV").write_text(
        "file,page,log_number\na.pdf,1,1234500\n", encoding="utf-8"
    )
    os.utime(run, (mtime, mtime))
    return run


def test_las_corridas_se_listan_de_la_mas_reciente_a_la_mas_antigua(tmp_path: Path):
    vieja = _run(tmp_path, "BITS 16 AUG 2026 20 54", 1_000_000)
    nueva = _run(tmp_path, "BITS 18 AUG 2026 05 42", 3_000_000)
    media = _run(tmp_path, "BITS 17 AUG 2026 05 50", 2_000_000)
    # Junto a las corridas viven carpetas que no lo son.
    (tmp_path / "logs").mkdir()
    (tmp_path / "firmas_dataset").mkdir()
    (tmp_path / ".performance.json").write_text("{}", encoding="utf-8")

    assert find_run_dirs(tmp_path) == [nueva, media, vieja]
    assert find_run_dirs(tmp_path, 2) == [nueva, media]
    assert find_run_dirs(tmp_path / "no_existe") == []


def test_las_corridas_historicas_con_el_csv_en_la_raiz_tambien_cuentan(tmp_path: Path):
    antigua = tmp_path / "BITS VIEJO"
    antigua.mkdir()
    (antigua / "BITS VIEJO.CSV").write_text("file,page\na.pdf,1\n", encoding="utf-8")

    assert find_run_dirs(tmp_path) == [antigua]


def test_el_historial_abre_la_corrida_elegida(tmp_path: Path):
    app = QApplication.instance() or QApplication([])
    _run(tmp_path, "BITS 16 AUG 2026 20 54", 1_000_000)
    nueva = _run(tmp_path, "BITS 18 AUG 2026 05 42", 3_000_000)
    viewer = CsvViewerWindow(tmp_path)
    try:
        assert [
            viewer.history_combo.itemText(index)
            for index in range(viewer.history_combo.count())
        ] == ["BITS 18 AUG 2026 05 42", "BITS 16 AUG 2026 20 54"]

        viewer._on_history_activated(0)

        assert viewer._folder == nueva
        assert viewer.table.rowCount() == 1
        # La corrida abierta queda marcada en la lista, se haya llegado a ella
        # por el historial o buscándola a mano.
        viewer.load_folder(tmp_path / "BITS 16 AUG 2026 20 54")
        assert viewer.history_combo.currentText() == "BITS 16 AUG 2026 20 54"
    finally:
        viewer.pdf_viewer.shutdown()
        viewer.close()
        app.processEvents()


def test_sin_corridas_el_historial_lo_dice_y_no_se_puede_usar(tmp_path: Path):
    app = QApplication.instance() or QApplication([])
    viewer = CsvViewerWindow(tmp_path)
    try:
        assert not viewer.history_combo.isEnabled()
        assert "No hay corridas" in viewer.history_combo.currentText()
        assert viewer.history_combo.currentData() is None
    finally:
        viewer.pdf_viewer.shutdown()
        viewer.close()
        app.processEvents()


def test_las_salidas_arrancan_en_un_pdf_por_matricula_con_discrepancias():
    QApplication.instance() or QApplication([])
    options = ExportOptionsGroup()

    assert options.radio_unico.isChecked()
    assert not options.radio_varios.isChecked()
    assert options.matricula_check.isChecked()
    assert not options.mes_check.isChecked()
    assert options.separar_por() == ["avion"]
    # Lo que se marca es una sospecha, y el texto lo dice igual que el PDF.
    assert options.discrepancias_check.text() == "Posibles discrepancias"
    assert options.discrepancias_check.isChecked()
    assert not options.errores_check.isChecked()

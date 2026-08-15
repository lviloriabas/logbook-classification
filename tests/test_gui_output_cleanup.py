"""Contrato del botón que vacía output/ de forma recuperable."""

from __future__ import annotations

import os
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

from app.gui.main_window import MainWindow


def test_clear_output_moves_all_contents_to_trash(tmp_path):
    app = QApplication.instance() or QApplication([])
    output = tmp_path / "output"
    run = output / "BITS TEST"
    run.mkdir(parents=True)
    cache = output / ".performance.json"
    cache.write_text("{}", encoding="utf-8")
    window = MainWindow()
    window._corrida_dir = run
    try:
        with patch("app.gui.main_window.SCRIPT_DIR", tmp_path), patch(
            "app.gui.main_window.QMessageBox.warning",
            return_value=QMessageBox.StandardButton.Yes,
        ), patch(
            "app.gui.main_window.QMessageBox.information"
        ), patch(
            "app.gui.main_window.send_to_trash",
            return_value=([cache, run], []),
        ) as trash:
            window._clear_output_folder()

        assert window.btn_clear_output.text() == "Vaciar output"
        assert set(trash.call_args.args[0]) == {cache, run}
        assert window._corrida_dir is None
    finally:
        window.close()
        app.processEvents()

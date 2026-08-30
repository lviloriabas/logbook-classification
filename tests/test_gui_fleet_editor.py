from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.gui.fleet_editor import FleetEditorDialog, FleetStore


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _visible_values(dialog: FleetEditorDialog) -> list[str]:
    return [
        dialog.values.item(index).text()
        for index in range(dialog.values.count())
        if not dialog.values.item(index).isHidden()
    ]


def test_escribir_filtra_las_matriculas_existentes(tmp_path: Path):
    _app()
    store = FleetStore(tmp_path / "fleet.json")
    store.save(["HP-1234CMP", "HP-5678CMP", "HP-5678WWP"])
    dialog = FleetEditorDialog(store)
    try:
        dialog.entry.setText("hp 5678")
        assert _visible_values(dialog) == ["HP-5678CMP", "HP-5678WWP"]

        dialog.entry.setText("1234")
        assert _visible_values(dialog) == ["HP-1234CMP"]

        dialog.entry.clear()
        assert _visible_values(dialog) == [
            "HP-1234CMP",
            "HP-5678CMP",
            "HP-5678WWP",
        ]
    finally:
        dialog.close()


def test_agregar_restablece_la_lista_sin_duplicar(tmp_path: Path):
    _app()
    store = FleetStore(tmp_path / "fleet.json")
    store.save(["HP-1234CMP", "HP-5678CMP"])
    dialog = FleetEditorDialog(store)
    try:
        dialog.entry.setText("HP-1234CMP")
        assert _visible_values(dialog) == ["HP-1234CMP"]

        dialog._add_value()

        assert dialog.entry.text() == ""
        assert _visible_values(dialog) == ["HP-1234CMP", "HP-5678CMP"]
        assert dialog.values.count() == 2
    finally:
        dialog.close()

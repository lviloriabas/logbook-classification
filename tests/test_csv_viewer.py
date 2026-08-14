"""Selección y filtrado de columnas del visor CSV."""

from __future__ import annotations

from pathlib import Path
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from app.gui.csv_viewer import (
    CsvColumnModeButton,
    CsvViewerWindow,
    source_pdf_paths_for_rows,
)
from app.gui.csv_utils import (
    csv_field_id,
    find_csv_files,
    important_csv_columns,
    infer_important_field_ids,
    read_csv_file,
)


def _columns() -> list[str]:
    return [
        "file",
        "page",
        "log_number",
        "dup",
        "log_number_conf",
        "log_number_status",
        "matricula",
        "pilot_signature",
        "captain_license",
        "day",
        "month",
        "year",
        "day_1",
        "date",
        "time_ms",
    ]


def test_important_view_keeps_only_primary_values_and_run_columns():
    visible = important_csv_columns(
        _columns(),
        {"log_number", "matricula", "pilot_signature", "day", "month", "year"},
    )

    assert visible == [
        "file",
        "page",
        "log_number",
        "dup",
        "matricula",
        "pilot_signature",
        "date",
        "time_ms",
    ]


def test_complete_field_id_is_recovered_from_metadata_column():
    assert csv_field_id("log_number_status", _columns()) == "log_number"
    assert csv_field_id("pilot_signature", _columns()) == "pilot_signature"
    assert csv_field_id("dup", _columns()) is None
    assert csv_field_id("date", _columns()) is None


def test_fallback_importance_includes_signatures_but_not_cell_fields():
    assert infer_important_field_ids(_columns()) == {
        "log_number",
        "matricula",
        "captain_license",
        "pilot_signature",
    }


def test_find_and_read_csv_from_processed_data_folder(tmp_path: Path):
    run = tmp_path / "BITS TEST"
    data = run / "datos"
    data.mkdir(parents=True)
    csv_path = data / "BITS TEST.CSV"
    csv_path.write_text(
        "\ufefffile,page,matricula,date\nbitácora.pdf,1,HP-1234CMP,2026/08/13\n",
        encoding="utf-8",
    )

    assert find_csv_files(run) == [csv_path]
    columns, rows = read_csv_file(csv_path)
    assert columns == ["file", "page", "matricula", "date"]
    assert rows == [
        {
            "file": "bitácora.pdf",
            "page": "1",
            "matricula": "HP-1234CMP",
            "date": "2026/08/13",
        }
    ]


def test_column_mode_control_is_compact_icon_only():
    app = QApplication.instance() or QApplication([])
    button = CsvColumnModeButton()

    assert button.width() == button.height() == 30
    assert button.text() == ""
    assert not button.icon().isNull()
    assert "campos importantes" in button.toolTip()

    button.setChecked(False)
    app.processEvents()
    assert "CSV completo" in button.toolTip()


def test_true_dup_uses_warning_color_convention():
    app = QApplication.instance() or QApplication([])
    viewer = CsvViewerWindow(Path("."))

    assert viewer._status_for({"dup": "true"}, "dup") == "WARNING"
    assert viewer._status_for({"dup": "false"}, "dup") is None

    viewer.close()
    app.processEvents()


def test_column_control_is_hidden_until_a_csv_is_loaded(tmp_path: Path):
    app = QApplication.instance() or QApplication([])
    viewer = CsvViewerWindow(tmp_path)
    assert viewer.column_toggle.isHidden()

    run = tmp_path / "run"
    data = run / "datos"
    data.mkdir(parents=True)
    (data / "run.csv").write_text(
        "file,page,matricula,date\na.pdf,1,HP-1234CMP,2026/08/13\n",
        encoding="utf-8",
    )
    assert viewer.load_folder(run) is True
    app.processEvents()
    assert not viewer.column_toggle.isHidden()


def test_companion_json_resolves_source_pdf_per_csv_row(tmp_path: Path):
    source_a = tmp_path / "source-a" / "same.pdf"
    source_b = tmp_path / "source-b" / "same.pdf"
    data = tmp_path / "run" / "datos"
    data.mkdir(parents=True)
    csv_path = data / "run.csv"
    csv_path.write_text("file,page,log_number\nsame.pdf,1,1234500\nsame.pdf,1,2234500\n")
    csv_path.with_suffix(".json").write_text(
        __import__("json").dumps(
            {
                "reportes": [
                    {"pdf_path": str(source_a), "pages": [{"page_number": 1}]},
                    {"pdf_path": str(source_b), "pages": [{"page_number": 1}]},
                ]
            }
        ),
        encoding="utf-8",
    )

    rows = [
        {"file": "same.pdf", "page": "1", "log_number": "1234500"},
        {"file": "same.pdf", "page": "1", "log_number": "2234500"},
    ]
    assert source_pdf_paths_for_rows(csv_path, rows) == [source_a, source_b]


def test_log_search_selects_exact_seven_digit_match(tmp_path: Path):
    app = QApplication.instance() or QApplication([])
    run = tmp_path / "run"
    data = run / "datos"
    data.mkdir(parents=True)
    (data / "run.csv").write_text(
        "file,page,log_number\na.pdf,3,1234500\nb.pdf,7,1234501\n",
        encoding="utf-8",
    )
    viewer = CsvViewerWindow(tmp_path)
    assert viewer.load_folder(run)

    viewer.log_search.setText("1234501")
    viewer._find_log_number()

    assert viewer._search_matches == [1]
    assert viewer.table.currentRow() == 1
    assert "b.pdf, página 7" in viewer.search_context.text()
    viewer.close()
    app.processEvents()

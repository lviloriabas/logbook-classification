"""Ordenamiento de tres estados al hacer clic en las columnas del CSV."""

from __future__ import annotations

from pathlib import Path
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from app.gui.csv_viewer import CsvViewerWindow
from app.gui.main_window import MainWindow
from app.gui.table_sort import sorted_row_order
from app.models.schemas import FieldResult, PageResult, ValidationReport
from app.templates.schema import FieldTemplate, Template


def _click(table, column: int) -> None:
    table.horizontalHeader().sectionClicked.emit(column)


def _column_texts(table, column: int) -> list[str]:
    return [
        table.item(row, column).text() if table.item(row, column) else ""
        for row in range(table.rowCount())
    ]


def _viewer_with_csv(tmp_path: Path) -> CsvViewerWindow:
    run = tmp_path / "run"
    data = run / "datos"
    data.mkdir(parents=True)
    (data / "run.csv").write_text(
        "file,page,log_number\n"
        "b.pdf,9,1234501\n"
        "a.pdf,10,1234502\n"
        "c.pdf,2,1234500\n",
        encoding="utf-8",
    )
    viewer = CsvViewerWindow(tmp_path)
    assert viewer.load_folder(run)
    return viewer


def test_numeric_columns_sort_as_numbers_and_blanks_stay_last():
    assert sorted_row_order(["9", "10", "2"], descending=True) == [1, 0, 2]
    assert sorted_row_order(["9", "10", "2"], descending=False) == [2, 0, 1]
    assert sorted_row_order(["b", "", "a"], descending=True) == [0, 2, 1]
    assert sorted_row_order(["b", "", "a"], descending=False) == [2, 0, 1]


def test_header_clicks_cycle_descending_ascending_and_original(tmp_path: Path):
    app = QApplication.instance() or QApplication([])
    viewer = _viewer_with_csv(tmp_path)
    table = viewer.table
    log_column = 2

    _click(table, log_column)
    assert _column_texts(table, log_column) == ["1234502", "1234501", "1234500"]
    assert table.horizontalHeader().sortIndicatorOrder() == (
        Qt.SortOrder.DescendingOrder
    )

    _click(table, log_column)
    assert _column_texts(table, log_column) == ["1234500", "1234501", "1234502"]

    _click(table, log_column)
    assert _column_texts(table, log_column) == ["1234501", "1234502", "1234500"]
    assert not table.horizontalHeader().isSortIndicatorShown()

    viewer.close()
    app.processEvents()


def test_sorting_moves_the_whole_row_and_its_metadata(tmp_path: Path):
    app = QApplication.instance() or QApplication([])
    viewer = _viewer_with_csv(tmp_path)
    table = viewer.table

    _click(table, 1)  # página, de mayor a menor

    assert _column_texts(table, 0) == ["a.pdf", "b.pdf", "c.pdf"]
    assert _column_texts(table, 1) == ["10", "9", "2"]
    # El rol de fila original acompaña a la celda: la búsqueda sigue ubicándola.
    assert [
        table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        for row in range(table.rowCount())
    ] == [1, 0, 2]

    viewer.search_edit.setText("1234500")
    viewer._find_in_csv()
    assert table.currentRow() == 2

    viewer.close()
    app.processEvents()


def test_changing_csv_starts_again_without_order(tmp_path: Path):
    app = QApplication.instance() or QApplication([])
    viewer = _viewer_with_csv(tmp_path)

    _click(viewer.table, 2)
    assert viewer.table_sort.sorted_column == 2

    other = tmp_path / "other"
    data = other / "datos"
    data.mkdir(parents=True)
    (data / "other.csv").write_text(
        "file,page,log_number\nz.pdf,4,1234509\ny.pdf,1,1234503\n",
        encoding="utf-8",
    )
    assert viewer.load_folder(other)

    assert viewer.table_sort.sorted_column == -1
    assert _column_texts(viewer.table, 2) == ["1234509", "1234503"]

    viewer.close()
    app.processEvents()


def test_main_window_table_sorts_by_column_click():
    app = QApplication.instance() or QApplication([])
    template = Template(
        name="fixture",
        fields=[FieldTemplate(id="log_number", x=0.1, y=0.1, w=0.2, h=0.1)],
    )
    reports = [
        ValidationReport(
            pdf_path="first.pdf",
            template_name="fixture",
            pages=[
                PageResult(
                    page_number=number,
                    fields=[
                        FieldResult(
                            page_number=number,
                            field_id="log_number",
                            field_type="ocr",
                            value=value,
                        )
                    ],
                )
                for number, value in (
                    (11, "2147300"),
                    (2, "2147301"),
                    (7, "2147302"),
                )
            ],
        )
    ]
    window = MainWindow()
    try:
        window._processed_template = template
        window._populate_table(reports)
        window._table_timer.stop()
        # Mientras la tabla se llena por lotes, los clics no reordenan nada.
        _click(window.table, 1)
        assert window.table_sort.sorted_column == -1
        while window._table_pending:
            window._on_table_chunk()

        page_column = window._table_columns.index("page")
        _click(window.table, page_column)
        assert _column_texts(window.table, page_column) == ["11", "7", "2"]

        _click(window.table, page_column)
        assert _column_texts(window.table, page_column) == ["2", "7", "11"]

        _click(window.table, page_column)
        assert _column_texts(window.table, page_column) == ["11", "2", "7"]
        assert window.table_sort.sorted_column == -1
    finally:
        window.close()
        app.processEvents()

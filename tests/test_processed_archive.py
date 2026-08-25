"""Los archivos procesados salen de input/ y la ventana los sigue hasta allí.

La entrada solo debe mostrar lo que falta por procesar. Lo ya procesado se
guarda en ``input/processed``, y todo lo que la ventana tenía apuntando al
archivo (los reportes, la vista previa, las filas de la tabla) tiene que
apuntar a la ruta nueva: si no, la ejecución recién terminada se queda sin
vista previa y sin poder exportarse otra vez.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QTableWidgetItem

from app.gui.main_window import MainWindow
from app.reports.json_reporter import JsonReporter
from app.utils.io import PROCESSED_DIRNAME, archive_processed_files


def _pdf(path: Path, content: str = "%PDF-1.4\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_solo_se_aparta_lo_que_estaba_en_la_entrada(tmp_path: Path):
    entrada = tmp_path / "input"
    ajena = _pdf(tmp_path / "Documentos" / "de_otra_carpeta.pdf")
    propia = _pdf(entrada / "bitacora.pdf")

    moved, failed = archive_processed_files([propia, ajena], entrada)

    assert not failed
    # La carpeta del usuario no es del programa: ahí no se mueve nada.
    assert ajena.is_file()
    assert not propia.exists()
    assert moved == {propia: entrada / PROCESSED_DIRNAME / "bitacora.pdf"}
    assert moved[propia].is_file()


def test_el_nombre_repetido_no_pisa_al_ya_procesado(tmp_path: Path):
    entrada = tmp_path / "input"
    _pdf(entrada / PROCESSED_DIRNAME / "bitacora.pdf", "%PDF-1.4 anterior\n")
    nuevo = _pdf(entrada / "bitacora.pdf", "%PDF-1.4 nuevo\n")

    moved, failed = archive_processed_files([nuevo], entrada)

    assert not failed
    archivo = entrada / PROCESSED_DIRNAME
    assert moved[nuevo] == archivo / "bitacora-2.pdf"
    assert (archivo / "bitacora.pdf").read_text(encoding="utf-8").endswith("anterior\n")
    assert (archivo / "bitacora-2.pdf").read_text(encoding="utf-8").endswith("nuevo\n")


def test_lo_ya_apartado_no_se_vuelve_a_mover(tmp_path: Path):
    entrada = tmp_path / "input"
    apartado = _pdf(entrada / PROCESSED_DIRNAME / "bitacora.pdf")

    moved, failed = archive_processed_files([apartado], entrada)

    assert (moved, failed) == ({}, [])
    assert apartado.is_file()


def test_sin_nada_que_mover_no_se_crea_la_carpeta(tmp_path: Path):
    entrada = tmp_path / "input"
    entrada.mkdir()

    moved, failed = archive_processed_files([entrada / "no_existe.pdf"], entrada)

    assert (moved, failed) == ({}, [])
    assert not (entrada / PROCESSED_DIRNAME).exists()


def _report(pdf_path: Path):
    from app.models.schemas import ValidationReport

    return ValidationReport(pdf_path=str(pdf_path), template_name="t", pages=[])


def test_la_ventana_reapunta_a_la_carpeta_de_procesados(tmp_path: Path):
    """Terminadas las salidas, la ejecución sigue completa con los PDF movidos."""
    app = QApplication.instance() or QApplication([])
    entrada = tmp_path / "input"
    pdf = _pdf(entrada / "bitacora.pdf")
    window = MainWindow()
    try:
        window._reports = [_report(pdf)]
        window._pdf_paths = [pdf]
        window._row_pdfs = [pdf]
        window._table_columns = ["page"]
        window.table.setColumnCount(1)
        window.table.setRowCount(1)
        page_item = QTableWidgetItem("1")
        page_item.setData(Qt.ItemDataRole.UserRole, 1)
        page_item.setData(Qt.ItemDataRole.UserRole + 1, str(pdf))
        window.table.setItem(0, 0, page_item)
        window._preview_pdf = pdf
        window._preview_results = {(str(pdf.resolve()), 1): object()}
        window._preprocess_geometry = {(str(pdf), 1): {"skew_angle": 0.0}}

        with patch("app.gui.main_window.SCRIPT_DIR", tmp_path):
            window._archive_processed_inputs()

        apartado = entrada / PROCESSED_DIRNAME / "bitacora.pdf"
        assert apartado.is_file()
        assert not pdf.exists()
        assert window._reports[0].pdf_path == str(apartado)
        assert window._pdf_paths == [apartado]
        assert window._row_pdfs == [apartado]
        assert page_item.data(Qt.ItemDataRole.UserRole + 1) == str(apartado)
        assert window._preview_pdf == apartado
        assert list(window._preview_results) == [(str(apartado.resolve()), 1)]
        assert list(window._preprocess_geometry) == [(str(apartado), 1)]
    finally:
        window.close()
        app.processEvents()


def test_la_carpeta_de_procesados_existe_desde_el_arranque(tmp_path: Path):
    """No aparece el día que termina la primera ejecución: está desde el inicio."""
    app = QApplication.instance() or QApplication([])
    entrada = tmp_path / "input"
    entrada.mkdir()
    window = MainWindow()
    try:
        with patch("app.gui.main_window.SCRIPT_DIR", tmp_path):
            window.load_initial_data()

        assert (entrada / PROCESSED_DIRNAME).is_dir()
    finally:
        window.close()
        app.processEvents()


def test_una_corrida_cancelada_deja_los_archivos_en_la_entrada(tmp_path: Path):
    """Cancelar no procesa el batch entero: esos archivos siguen pendientes."""
    app = QApplication.instance() or QApplication([])
    entrada = tmp_path / "input"
    pdf = _pdf(entrada / "bitacora.pdf")
    window = MainWindow()
    try:
        window._reports = [_report(pdf)]
        window._last_run_cancelled = True
        window._outputs_context = "proceso"

        with patch("app.gui.main_window.SCRIPT_DIR", tmp_path):
            window._on_outputs_written(tmp_path / "output" / "BITS TEST")

        assert pdf.is_file()
        assert not (entrada / PROCESSED_DIRNAME).exists()
    finally:
        window.close()
        app.processEvents()


def test_el_visor_encuentra_los_pdf_ya_apartados(tmp_path: Path):
    """El CSV apunta a input/; el original está en input/processed y vale igual."""
    from app.gui import csv_viewer

    entrada = tmp_path / "input"
    original = entrada / "bitacora.pdf"
    apartado = _pdf(entrada / PROCESSED_DIRNAME / "bitacora.pdf")
    run = tmp_path / "output" / "BITS TEST"
    datos = run / "datos"
    datos.mkdir(parents=True)
    csv_path = datos / "BITS TEST.CSV"
    csv_path.write_text(
        "file,page\nbitacora.pdf,1\n", encoding="utf-8"
    )
    (datos / "BITS TEST.json").write_text(
        '{"reportes": [{"pdf_path": "%s", "pages": [{"page_number": 1}]}]}'
        % str(original).replace("\\", "\\\\"),
        encoding="utf-8",
    )

    with patch.object(csv_viewer, "_PROGRAM_DIR", tmp_path):
        _rows, available, missing = csv_viewer.resolve_source_documents(
            csv_path, [{"file": "bitacora.pdf", "page": "1"}]
        )

    assert missing == []
    assert available == [apartado]


def test_el_visor_prefiere_processed_sobre_un_homonimo_nuevo_en_input(
    tmp_path: Path,
):
    """Un archivo pendiente no puede reemplazar la fuente de una ejecución."""
    from app.gui import csv_viewer

    entrada = tmp_path / "input"
    anterior = _pdf(
        entrada / PROCESSED_DIRNAME / "bitacora.pdf", "%PDF anterior\n"
    )
    _pdf(entrada / "bitacora.pdf", "%PDF nuevo\n")
    run = tmp_path / "output" / "BITS TEST"
    datos = run / "datos"
    datos.mkdir(parents=True)
    csv_path = datos / "BITS TEST.CSV"
    csv_path.write_text("file,page\nbitacora.pdf,1\n", encoding="utf-8")
    (datos / "BITS TEST.json").write_text(
        '{"reportes": [{"pdf_path": "C:\\\\OtroEquipo\\\\BITS\\\\input\\\\bitacora.pdf", '
        '"pages": [{"page_number": 1}]}]}',
        encoding="utf-8",
    )

    with patch.object(csv_viewer, "_PROGRAM_DIR", tmp_path):
        rows, available, missing = csv_viewer.resolve_source_documents(
            csv_path, [{"file": "bitacora.pdf", "page": "1"}]
        )

    assert missing == []
    assert available == [anterior]
    assert rows == [anterior]


def test_el_json_guarda_el_sufijo_del_archivo_que_acaba_de_procesar(
    tmp_path: Path,
):
    """La siguiente apertura debe elegir ``-2``, no el homónimo anterior."""
    from app.gui import csv_viewer
    from app.models.schemas import PageResult

    app = QApplication.instance() or QApplication([])
    entrada = tmp_path / "input"
    anterior = _pdf(
        entrada / PROCESSED_DIRNAME / "bitacora.pdf", "%PDF anterior\n"
    )
    nuevo = _pdf(entrada / "bitacora.pdf", "%PDF nuevo\n")
    run = tmp_path / "output" / "BITS TEST"
    datos = run / "datos"
    datos.mkdir(parents=True)
    csv_path = datos / "BITS TEST.CSV"
    csv_path.write_text("file,page\nbitacora.pdf,1\n", encoding="utf-8")
    report = _report(nuevo)
    report.pages = [PageResult(page_number=1)]
    json_path = datos / "BITS TEST.json"
    JsonReporter().write_consolidated([report], json_path, corrida=run.name)

    window = MainWindow()
    try:
        window._reports = [report]
        window._pdf_paths = [nuevo]
        window._row_pdfs = [nuevo]
        with patch("app.gui.main_window.SCRIPT_DIR", tmp_path):
            window._archive_processed_inputs(run)

        destino = entrada / PROCESSED_DIRNAME / "bitacora-2.pdf"
        payload = __import__("json").loads(json_path.read_text(encoding="utf-8"))
        assert payload["reportes"][0]["pdf_path"] == str(destino)
        assert payload["reportes"][0]["source_name"] == "bitacora.pdf"
        assert report.source_filename == "bitacora.pdf"

        with patch.object(csv_viewer, "_PROGRAM_DIR", tmp_path):
            rows, available, missing = csv_viewer.resolve_source_documents(
                csv_path, [{"file": "bitacora.pdf", "page": "1"}]
            )

        assert anterior.is_file()
        assert missing == []
        assert available == [destino]
        assert rows == [destino]
    finally:
        window.close()
        app.processEvents()


def test_una_ejecucion_de_otro_equipo_conserva_su_sufijo_en_processed(
    tmp_path: Path,
):
    """Otra unidad o carpeta no cambia qué copia identifica ``-2``."""
    from app.gui import csv_viewer

    entrada = tmp_path / "input"
    _pdf(entrada / PROCESSED_DIRNAME / "bitacora.pdf", "%PDF anterior\n")
    esperada = _pdf(
        entrada / PROCESSED_DIRNAME / "bitacora-2.pdf", "%PDF correcta\n"
    )
    run = tmp_path / "output" / "BITS TEST"
    datos = run / "datos"
    datos.mkdir(parents=True)
    csv_path = datos / "BITS TEST.CSV"
    csv_path.write_text("file,page\nbitacora.pdf,1\n", encoding="utf-8")
    (datos / "BITS TEST.json").write_text(
        '{"reportes": [{"pdf_path": "C:\\\\OtroEquipo\\\\BITS\\\\input\\\\processed\\\\bitacora-2.pdf", '
        '"pages": [{"page_number": 1}]}]}',
        encoding="utf-8",
    )

    with patch.object(csv_viewer, "_PROGRAM_DIR", tmp_path):
        rows, available, missing = csv_viewer.resolve_source_documents(
            csv_path, [{"file": "bitacora.pdf", "page": "1"}]
        )

    assert missing == []
    assert available == [esperada]
    assert rows == [esperada]

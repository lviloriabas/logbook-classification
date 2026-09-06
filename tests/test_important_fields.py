"""Memoria de campos importantes y recuadros que dibuja la vista previa."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtWidgets import QApplication

from app.gui import csv_viewer
from app.gui.csv_utils import (
    template_field_ids_for_columns,
    template_name_for_csv,
)
from app.gui.csv_viewer import CsvViewerWindow
from app.gui.main_window import _visible_preview_fields
from app.templates.schema import FieldTemplate, Template
from app.utils.important_fields import ImportantFieldsStore


def _template() -> Template:
    """Plantilla con campos obligatorios y opcionales, como la real."""
    return Template(
        name="preview",
        fields=[
            FieldTemplate(id="log_number", x=0.1, y=0.1, w=0.2, h=0.1, required=True),
            FieldTemplate(id="captain_license", x=0.4, y=0.1, w=0.2, h=0.1),
            FieldTemplate(id="day", x=0.7, y=0.1, w=0.1, h=0.1, required=True),
            FieldTemplate(id="day_1", x=0.7, y=0.3, w=0.1, h=0.1),
        ],
    )


def _columns() -> list[str]:
    return [
        "file",
        "page",
        "log_number",
        "log_number_conf",
        "log_number_status",
        "captain_license",
        "captain_license_conf",
        "day",
        "day_1",
        "date",
        "time_ms",
    ]


def test_store_separates_templates_and_remembers_an_empty_selection(
    tmp_path: Path,
):
    path = tmp_path / "important_fields.json"
    store = ImportantFieldsStore(path)

    assert store.load("Aircraft Log") is None
    store.save("Aircraft Log", {"log_number", "captain_license"})
    store.save("Otra", set())

    # Una sesión nueva lee el mismo archivo portable.
    reopened = ImportantFieldsStore(path)
    assert reopened.load("Aircraft Log") == {"log_number", "captain_license"}
    assert reopened.load("Otra") == set()
    assert reopened.load("Sin editar") is None


def test_editar_la_lista_no_borra_lo_que_el_selector_no_pudo_ensenar(
    tmp_path: Path,
):
    """Un selector abierto sobre menos columnas no habla por las demás.

    El CSV mínimo trae solo las columnas marcadas, y el de una corrida
    anterior ni siquiera trae las que se marcaron después. Guardando solo
    lo que el cuadro enseñó, tocar la lista desde ahí borraba en silencio
    el resto: la selección se editaba una y otra vez y nunca se quedaba.
    """
    store = ImportantFieldsStore(tmp_path / "important_fields.json")
    store.save("Aircraft Log", {"file", "log_number", "technician_license"})

    # El cuadro solo pudo enseñar las columnas de esa corrida.
    quedo = store.save(
        "Aircraft Log", {"file"}, scope=["file", "log_number", "matricula"]
    )

    assert quedo == {"file", "technician_license"}
    assert store.load("Aircraft Log") == {"file", "technician_license"}
    # Sin ``scope`` sigue mandando la lista completa: es el cuadro que las
    # enseñó todas y desmarcar tiene que poder dejarla vacía.
    assert store.save("Aircraft Log", set()) == set()
    assert store.load("Aircraft Log") == set()


def test_store_ignores_a_damaged_file_without_raising(tmp_path: Path):
    path = tmp_path / "important_fields.json"
    path.write_text("{no es json", encoding="utf-8")

    assert ImportantFieldsStore(path).load("Aircraft Log") is None


def test_marked_columns_resolve_to_the_fields_that_produce_them():
    field_ids = ["log_number", "captain_license", "day", "day_1"]

    # Las columnas de metadatos apuntan al campo de su valor.
    assert template_field_ids_for_columns(
        {"log_number_status"}, field_ids, _columns()
    ) == {"log_number"}
    # ``date`` representa las casillas consolidadas de la fecha.
    assert template_field_ids_for_columns({"date"}, field_ids, _columns()) == {
        "day"
    }
    # Las columnas de ejecución no son campos de la plantilla.
    assert template_field_ids_for_columns(
        {"file", "page", "time_ms"}, field_ids, _columns()
    ) == set()


def test_preview_draws_the_marked_fields_even_if_they_are_not_required():
    template = _template()

    assert [f.id for f in _visible_preview_fields(template, False)] == [
        "log_number",
        "captain_license",
        "day",
        "day_1",
    ]
    assert [
        f.id
        for f in _visible_preview_fields(
            template, True, {"captain_license", "day_1"}
        )
    ] == ["captain_license", "day_1"]
    # Sin selección manda la importancia declarada por la plantilla.
    assert [f.id for f in _visible_preview_fields(template, True, None)] == [
        "log_number",
        "day",
    ]


def test_main_window_preview_follows_the_edited_list_and_remembers_it(
    window, tmp_path: Path
):
    store_path = tmp_path / "important_fields.json"
    window._important_fields_store = ImportantFieldsStore(store_path)
    template = _template()
    columns = _columns()
    window._table_columns = columns
    window.table.setColumnCount(len(columns))
    window.fields_check.setChecked(True)
    window.important_fields_check.setChecked(True)

    window._set_important_columns({"log_number", "captain_license", "date"})

    ids = window._current_important_field_ids(template)
    assert ids == {"log_number", "captain_license", "day"}
    assert [
        field.id for field in _visible_preview_fields(template, True, ids)
    ] == ["log_number", "captain_license", "day"]

    # La selección queda escrita y se recupera en la siguiente sesión.
    assert ImportantFieldsStore(store_path).load(window._template_key()) == {
        "log_number",
        "captain_license",
        "date",
    }
    window._important_fields_user_selected = False
    window._selected_important_columns = set()
    window._restore_important_columns()
    assert window._important_fields_user_selected
    assert window._selected_important_columns == {
        "log_number",
        "captain_license",
        "date",
    }


def test_csv_viewer_reuses_the_stored_list_on_the_next_run(
    tmp_path: Path, monkeypatch
):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(csv_viewer, "_PROGRAM_DIR", tmp_path)
    run = tmp_path / "run"
    data = run / "datos"
    data.mkdir(parents=True)
    (data / "run.csv").write_text(
        "file,page,log_number,matricula\na.pdf,1,1234500,HP-1234CMP\n",
        encoding="utf-8",
    )

    viewer = CsvViewerWindow(tmp_path)
    assert viewer.load_folder(run)
    viewer._set_important_columns({"file", "log_number"})
    viewer.close()

    reopened = CsvViewerWindow(tmp_path)
    try:
        assert reopened.load_folder(run)
        assert reopened._selected_important_columns == {"file", "log_number"}
        assert reopened.table.isColumnHidden(
            reopened.table_model.column_of("matricula")
        )
        assert not reopened.table.isColumnHidden(
            reopened.table_model.column_of("log_number")
        )
    finally:
        reopened.close()
        app.processEvents()


def test_el_visor_conserva_lo_marcado_que_ese_csv_no_trae(
    tmp_path: Path, monkeypatch
):
    """La corrida es anterior a la marca, y su CSV mínimo no la lleva."""
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(csv_viewer, "_PROGRAM_DIR", tmp_path)
    run = tmp_path / "run"
    data = run / "datos"
    data.mkdir(parents=True)
    (data / "run.csv").write_text(
        "file,page,log_number,matricula\na.pdf,1,1234500,HP-1234CMP\n",
        encoding="utf-8",
    )
    store = ImportantFieldsStore(tmp_path / "important_fields.json")
    store.save(None, {"file", "log_number", "technician_license"})

    viewer = CsvViewerWindow(tmp_path)
    try:
        assert viewer.load_folder(run)
        viewer._set_important_columns({"file", "log_number", "matricula"})

        assert store.load(None) == {
            "file", "log_number", "matricula", "technician_license",
        }
    finally:
        viewer.close()
        app.processEvents()


def test_el_csv_completo_hereda_la_plantilla_del_minimo(tmp_path: Path):
    """No tiene JSON propio: la ejecución escribe uno solo, el del mínimo.

    Sin este respaldo, abrir el completo se quedaba sin plantilla y con
    ella se perdía la selección de campos importantes que esa plantilla
    tiene guardada: el visor abría el completo con el conjunto de fábrica
    aunque alguien hubiera elegido otro.
    """
    (tmp_path / "corrida.json").write_text(
        json.dumps({"reportes": [{"template_name": "Aircraft Log"}]}),
        encoding="utf-8",
    )
    minimo = tmp_path / "corrida.CSV"
    minimo.write_text("file,page\n", encoding="utf-8")
    completo = tmp_path / "corrida_completo.CSV"
    completo.write_text("file,page\n", encoding="utf-8")

    assert template_name_for_csv(minimo) == "Aircraft Log"
    assert template_name_for_csv(completo) == "Aircraft Log"


def test_un_csv_suelto_sin_json_no_inventa_plantilla(tmp_path: Path):
    suelto = tmp_path / "otro_completo.CSV"
    suelto.write_text("file,page\n", encoding="utf-8")

    assert template_name_for_csv(suelto) is None

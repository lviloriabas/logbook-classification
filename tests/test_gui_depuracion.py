"""El cuadro «Depurar páginas» y su botón en las dos vistas de CSV y PDF.

Comprueba lo que la ventana promete: que el conteo que se ve antes de borrar
sea el de la corrida abierta, que no se pueda eliminar sin marcar nada y que
el botón espere a tener una corrida guardada y ninguna escritura en curso.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox

from app.gui.csv_viewer import CsvViewerWindow
from app.gui.depuracion_dialog import DepurarPaginasDialog
from app.gui import main_window
from app.gui.main_window import MainWindow
from app.models.schemas import FieldResult, PageResult, ValidationReport

RAIZ = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def pagina(numero: int, log: str | None = None, blank: bool = False) -> PageResult:
    campos = []
    if log is not None:
        campos.append(
            FieldResult(
                page_number=numero,
                field_id="log_number",
                field_type="ocr",
                value=log,
            )
        )
    return PageResult(page_number=numero, blank=blank, fields=campos)


def corrida() -> list[ValidationReport]:
    """Un log_number repetido entre los dos PDF y una página en blanco."""
    return [
        ValidationReport(
            pdf_path="primero.pdf",
            template_name="fixture",
            pages=[pagina(1, "2147300"), pagina(2, blank=True)],
        ),
        ValidationReport(
            pdf_path="segundo.pdf",
            template_name="fixture",
            pages=[pagina(7, "2147300")],
        ),
    ]


def test_el_cuadro_enseña_cuantas_paginas_quita_cada_criterio(app):
    dialog = DepurarPaginasDialog(corrida())
    try:
        assert "1 página" in dialog.check_duplicados.text()
        assert "1 página" in dialog.check_blancas.text()
    finally:
        dialog.deleteLater()


def test_no_se_elimina_sin_marcar_ningun_criterio(app):
    dialog = DepurarPaginasDialog(corrida())
    try:
        assert not dialog.boton_eliminar.isEnabled()
        assert "Marque al menos un criterio" in dialog.total_label.text()

        dialog.check_duplicados.setChecked(True)

        assert dialog.boton_eliminar.isEnabled()
        assert dialog.resumen().total == 1
        assert "1 página" in dialog.total_label.text()
    finally:
        dialog.deleteLater()


def test_el_criterio_sin_paginas_queda_apagado(app):
    reports = [
        ValidationReport(
            pdf_path="unico.pdf",
            template_name="fixture",
            pages=[pagina(1, "2147300"), pagina(2, "2147301")],
        )
    ]
    dialog = DepurarPaginasDialog(reports)
    try:
        assert not dialog.check_duplicados.isEnabled()
        assert not dialog.check_blancas.isEnabled()
        assert not dialog.boton_eliminar.isEnabled()
        assert "no tiene páginas repetidas ni en blanco" in dialog.total_label.text()
    finally:
        dialog.deleteLater()


def test_marcar_los_dos_criterios_no_cuenta_dos_veces_la_misma_pagina(app):
    reports = [
        ValidationReport(
            pdf_path="unico.pdf",
            template_name="fixture",
            pages=[pagina(1, "2147300"), pagina(2, "2147300", blank=True)],
        )
    ]
    dialog = DepurarPaginasDialog(reports)
    try:
        dialog.check_duplicados.setChecked(True)
        dialog.check_blancas.setChecked(True)

        assert dialog.resumen().total == 1
    finally:
        dialog.deleteLater()


def test_el_boton_cancelar_esta_en_español(app):
    dialog = DepurarPaginasDialog(corrida())
    try:
        cancelar = dialog.buttons.button(
            QDialogButtonBox.StandardButton.Cancel
        )
        assert cancelar.text() == "Cancelar"
        assert dialog.boton_eliminar.text() == "Eliminar"
    finally:
        dialog.deleteLater()


def test_la_ventana_principal_no_depura_sin_corrida_guardada(app):
    window = MainWindow()
    try:
        assert not window.btn_depurar.isEnabled()

        window._reports = corrida()
        window._sync_depurar_button()
        # Todavía sin carpeta: la escritura reutiliza la de la corrida y sin
        # ella dejaría una segunda entrega de lo mismo.
        assert not window.btn_depurar.isEnabled()

        window._corrida_dir = RAIZ / "output" / "BITS 19 AUG 2026 05 00"
        window._sync_depurar_button()

        assert window.btn_depurar.isEnabled()

        window._last_run_cancelled = True
        window._sync_depurar_button()

        assert not window.btn_depurar.isEnabled()
    finally:
        window.close()
        app.processEvents()


class DialogoMarcado(DepurarPaginasDialog):
    """El cuadro real, con los dos criterios marcados y aceptado sin ratón."""

    def exec(self) -> int:  # noqa: A003 - API Qt
        self.check_duplicados.setChecked(True)
        self.check_blancas.setChecked(True)
        return QDialog.DialogCode.Accepted


def test_al_depurar_la_ventana_rehace_la_tabla_y_reescribe_la_corrida(app):
    """El recorrido entero: marcar, borrar, y lo que la pantalla enseña después."""
    from app.templates.schema import FieldTemplate, Template

    template = Template(
        name="fixture",
        fields=[FieldTemplate(id="log_number", x=0.1, y=0.1, w=0.2, h=0.1)],
    )
    window = MainWindow()
    escrituras = []
    try:
        window._processed_template = template
        window._reports = corrida()
        window._corrida_dir = RAIZ / "output" / "BITS 19 AUG 2026 05 00"
        window._sync_depurar_button()
        window._start_outputs = lambda reports, context, skip_pdfs=False: (
            escrituras.append((len(reports), context, skip_pdfs))
        )
        with patch.object(main_window, "DepurarPaginasDialog", DialogoMarcado):
            window._depurar_paginas()

        # De las tres páginas se van la repetida y la vacía; queda una sola,
        # y el PDF que se quedó sin ninguna sale de la corrida.
        assert [r.pdf_path for r in window._reports] == ["primero.pdf"]
        assert [p.page_number for p in window._reports[0].pages] == [1]

        window._table_timer.stop()
        while window._table_pending:
            window._on_table_chunk()
        assert window.table.rowCount() == 1
        assert window.duplicates_label.text() == "Duplicados: 0"

        # La reescritura va sobre la corrida y sin rehacer los PDF.
        assert escrituras == [(1, "depurar", True)]
    finally:
        window.close()
        app.processEvents()


def test_el_visor_de_csv_no_depura_un_csv_suelto(app, tmp_path):
    """Sin el JSON al lado no hay páginas que quitar, y el botón lo dice."""
    datos = tmp_path / "corrida" / "datos"
    datos.mkdir(parents=True)
    (datos / "corrida.csv").write_text(
        "file,page,log_number\na.pdf,1,2147300\n", encoding="utf-8"
    )
    window = CsvViewerWindow(tmp_path)
    try:
        assert window.load_csv_file(datos / "corrida.csv") is True
        assert not window.btn_depurar.isEnabled()
        assert "no viene acompañado del JSON" in window.btn_depurar.toolTip()
    finally:
        window.close()
        app.processEvents()

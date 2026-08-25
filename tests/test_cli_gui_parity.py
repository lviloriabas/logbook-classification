"""La línea de comandos y la ventana entregan la misma ejecución.

Las dos superficies escriben sus salidas con ``write_outputs``, así que la
paridad se reduce a una pregunta comprobable: ¿las dos rellenan las mismas
opciones? Cuando cada una escribía sus archivos por su cuenta, la respuesta
se fue separando sin que nada avisara (el CSV mínimo de la línea de comandos
salía con todas las columnas, sus stats nombraban PDFs que no existían y la
lista de aviones no se consultaba nunca). Esta prueba es la alarma.
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

from app.reports.outputs import OutputOptions
from run_cli import parse_args


_ROOT = Path(__file__).resolve().parents[1]
CLI_SOURCE = (_ROOT / "run_cli.py").read_text(encoding="utf-8")
GUI_SOURCE = "\n".join(
    (_ROOT / name).read_text(encoding="utf-8")
    for name in ("app/gui/main_window.py", "app/gui/csv_viewer.py")
)

# Plumbing: no son opciones del usuario, las pone quien llama.
_INTERNAS = {"template", "output_root", "run_dir"}
# Diferencias con motivo, no descuidos. Si aparece una cuarta, es un descuido.
_SOLO_VENTANA = {
    # Solo la ventana puede cancelar un batch a mitad de camino.
    "skip_pdfs",
}
_SOLO_LINEA_DE_COMANDOS = {
    # Volcado de recortes para auditar el detector de firmas a ojo.
    "recortes_firmas",
}


def _campos() -> set[str]:
    return {field.name for field in dataclasses.fields(OutputOptions)}


def test_la_linea_de_comandos_construye_sus_argumentos(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["run_cli.py", "--pdf", "una.pdf"])
    args = parse_args()
    assert args.pdf == "una.pdf"


def test_la_linea_de_comandos_rellena_todas_las_opciones_de_salida():
    faltan = {
        name for name in _campos() - _INTERNAS - _SOLO_VENTANA
        if f"{name}=" not in CLI_SOURCE
    }
    assert not faltan, f"run_cli.py no pasa: {sorted(faltan)}"


def test_la_ventana_rellena_todas_las_opciones_de_salida():
    faltan = {
        name for name in _campos() - _INTERNAS - _SOLO_LINEA_DE_COMANDOS
        if f"{name}=" not in GUI_SOURCE
    }
    assert not faltan, f"la ventana no pasa: {sorted(faltan)}"


def test_la_linea_de_comandos_llega_a_la_lista_de_aviones():
    """La verificación de flota existe en las dos, no solo en la ventana."""
    assert '"--verificar-flota"' in CLI_SOURCE
    assert "verify_fleet=args.verificar_flota" in CLI_SOURCE
    assert "verify_fleet=self.fleet_check.isChecked()" in GUI_SOURCE


def test_la_fecha_del_csv_se_elige_en_las_dos():
    assert '"--fecha-csv"' in CLI_SOURCE
    assert "csv_date_mode=" in CLI_SOURCE
    assert "csv_date_mode=" in GUI_SOURCE

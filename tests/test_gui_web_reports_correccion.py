"""La corrección automática vista desde la ventana de Web Reports."""

from __future__ import annotations

from app.airvault.config import AirVaultConfig
from app.airvault.correcciones import ACCION_BORRAR, ACCION_REINDEXAR
from app.airvault.web_reports import parsear_filas
from app.gui.web_reports_window import WebReportsWindow


def _fila(matricula: str, detalle: str) -> list[dict[str, str]]:
    return [
        {"t": matricula},
        {"t": "Copa-7 (50)"},
        {"t": ""},
        {"t": "2008150 - 2008199"},
        {"t": "1/1/2025 - 1/31/2025"},
        {"t": ""},
        {"t": ""},
        {"t": ""},
        {"t": ""},
        {"t": detalle},
    ]


def _excepciones(*detalles: tuple[str, str]):
    return parsear_filas(
        [_fila(matricula, detalle) for matricula, detalle in detalles],
        AirVaultConfig(),
    )


def test_corregir_empieza_apagado_y_no_toca_nada(app, tmp_path) -> None:
    ventana = WebReportsWindow(tmp_path)
    try:
        assert ventana.boton_corregir.text() == "Corregir…"
        assert not ventana.boton_corregir.isEnabled()
        assert ventana.plan() == []
    finally:
        ventana.close()


def test_se_enciende_con_lo_que_el_reporte_deja_resuelto(app, tmp_path) -> None:
    ventana = WebReportsWindow(tmp_path)
    try:
        ventana._al_recibir(
            _excepciones(
                ("HP-9913CMP", "DUPLICATED 2008159(3x)"),
                ("HP-9913CMP", "2008152 MIS-INDEX to ACN [HP-9813CMP]"),
            )
        )
        ventana._habilitar(True)

        assert [correccion.accion for correccion in ventana.plan()] == [
            ACCION_BORRAR,
            ACCION_REINDEXAR,
        ]
        assert ventana.boton_corregir.isEnabled()
        assert "Corregir…" in ventana.resumen.text()
    finally:
        ventana.close()


def test_sigue_apagado_si_todo_queda_para_revisar(app, tmp_path) -> None:
    """Una duplicada sin cuántas copias hay no la resuelve el reporte."""
    ventana = WebReportsWindow(tmp_path)
    try:
        ventana._al_recibir(
            _excepciones(("HP-9913CMP", "DUPLICATED 2008159(1x)"))
        )
        ventana._habilitar(True)

        assert ventana.plan()
        assert not ventana.boton_corregir.isEnabled()
        assert "Ninguna se puede corregir sola" in ventana.resumen.text()
    finally:
        ventana.close()


def test_no_autorizar_no_abre_el_navegador(app, tmp_path, monkeypatch) -> None:
    """Es la única puerta: sin el sí de alguien no se lanza ningún hilo."""
    ventana = WebReportsWindow(tmp_path)
    try:
        ventana._al_recibir(
            _excepciones(("HP-9913CMP", "DUPLICATED 2008159(3x)"))
        )
        monkeypatch.setattr(
            WebReportsWindow, "_autorizado", lambda self, plan: False
        )

        ventana._corregir()

        assert ventana.hilo() is None
    finally:
        ventana.close()


def test_el_cuadro_de_aviso_enumera_lo_que_se_va_a_hacer(
    app, tmp_path, monkeypatch
) -> None:
    visto: dict[str, object] = {}

    ventana = WebReportsWindow(tmp_path)
    try:
        ventana._al_recibir(
            _excepciones(
                ("HP-9913CMP", "DUPLICATED 2008159(3x)"),
                ("HP-1523CMP", "2284906 MIS-INDEX to ACN [HP-9813CMP]"),
            )
        )

        def _mirar(self, plan):
            visto["plan"] = list(plan)
            return False

        monkeypatch.setattr(WebReportsWindow, "_autorizado", _mirar)
        ventana._corregir()

        descripciones = [
            correccion.descripcion for correccion in visto["plan"]
        ]
        assert "conservar la más antigua de 3 copias" in descripciones[0]
        assert "de HP-9813CMP a HP-1523CMP" in descripciones[1]
    finally:
        ventana.close()

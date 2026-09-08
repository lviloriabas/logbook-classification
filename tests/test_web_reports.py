"""Consulta y presentación de Web Reports."""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import QDate

from app.airvault.config import AirVaultConfig
from app.airvault.web_reports import (
    TIPO_DUPLICADA,
    TIPO_MAL_INDEXADA,
    ClienteLogPageAudit,
    parsear_filas,
    url_busqueda_log,
)
from app.airvault import web_reports
from app.gui.web_reports_window import WebReportsWindow


def _fila(
    matricula: str,
    detalle: str,
    *,
    duplicadas: str = "",
) -> list[dict[str, str]]:
    return [
        {"t": matricula},
        {"t": "Copa-7 (50)"},
        {"t": ""},
        {"t": "2008150 - 2008199"},
        {"t": "1/1/2025 - 1/31/2025"},
        {"t": ""},
        {"t": duplicadas},
        {"t": ""},
        {"t": ""},
        {"t": detalle},
    ]


def test_parsea_duplicadas_y_mal_indexadas() -> None:
    config = AirVaultConfig()
    resultado = parsear_filas(
        [
            _fila("HP-9913CMP", "DUPLICATED 2008159(3x)", duplicadas="1"),
            _fila(
                "HP-9913CMP",
                "2008152 MIS-INDEX to ACN [HP-9813CMP]",
            ),
        ],
        config,
    )

    assert [item.tipo for item in resultado] == [
        TIPO_DUPLICADA,
        TIPO_MAL_INDEXADA,
    ]
    assert resultado[0].log_number == "2008159"
    assert resultado[0].copias == 3
    assert resultado[1].log_number == "2008152"
    assert resultado[1].destino == "HP-9813CMP"
    assert "repoId=3209" in resultado[1].url_busqueda
    assert "1=LOG%20PAGE%093=2008152%094=2008152" in (
        resultado[1].url_busqueda
    )


def test_descarta_cabeceras_y_repeticiones_del_html() -> None:
    config = AirVaultConfig()
    duplicada = _fila("HP-1523CMP", "DUPLICATED 2284906(2x)")

    resultado = parsear_filas(
        [
            [{"t": "AC#"}, {"t": "Book Type"}],
            duplicada,
            duplicada,
        ],
        config,
    )

    assert len(resultado) == 1


def test_construye_la_busqueda_con_el_repositorio_configurado() -> None:
    config = AirVaultConfig(
        base_url="https://airvault.example", repo_id=77
    )

    url = url_busqueda_log(config, "1234567")

    assert url.startswith("https://airvault.example/zfp/client/")
    assert "repoId=77" in url
    assert "searchId=15504" in url


def test_formatea_la_fecha_como_la_espera_ssrs() -> None:
    assert ClienteLogPageAudit._fecha_ssrs(date(2026, 9, 7)) == "9/7/2026"


def test_la_consulta_de_reportes_mantiene_edge_oculto(monkeypatch) -> None:
    visibles = []
    pestanas = []
    cerradas = []

    class _SesionFalsa:
        def __init__(self, _perfil, visible=True):
            visibles.append(visible)

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return None

        def abrir(self, _url, espera_s=30.0):
            return {"webSocketDebuggerUrl": "ws://127.0.0.1:4321/x"}

        def abrir_pestana(self, url):
            pestanas.append(url)
            return "x"

    class _PaginaFalsa:
        def __init__(self, *_args):
            pass

        def esperar(self, _condicion, _segundos):
            return True

        def cerrar(self):
            cerradas.append(True)

    monkeypatch.setattr(web_reports, "SesionDeNavegador", _SesionFalsa)
    monkeypatch.setattr(web_reports, "_Pagina", _PaginaFalsa)
    monkeypatch.setattr(
        ClienteLogPageAudit, "_elegir_repositorio", staticmethod(lambda _p: None)
    )
    monkeypatch.setattr(
        ClienteLogPageAudit, "_correr_reporte", lambda *_args: []
    )

    ClienteLogPageAudit(AirVaultConfig()).consultar(
        date(2026, 9, 1), date(2026, 9, 7), ["8"]
    )

    assert visibles == [False]
    # La pestana la abre la sesion (que de paso cierra las sobrantes) y se
    # cierra al terminar: el visor no se queda cargado en el perfil.
    assert pestanas == [web_reports.LOG_PAGE_AUDIT_URL]
    assert cerradas == [True]


def test_la_ventana_abre_en_el_mes_actual_y_solo_consulta(app, tmp_path) -> None:
    ventana = WebReportsWindow(tmp_path)
    hoy = QDate.currentDate()
    try:
        assert ventana.windowTitle() == "Web Reports"
        assert ventana.desde_edit.date() == QDate(hoy.year(), hoy.month(), 1)
        assert ventana.hasta_edit.date() == hoy
        assert ventana.filtro_combo.count() == 3
        assert ventana.tabla.columnCount() == 7
        assert "no modifica AirVault" in ventana.tabla.toolTip()
        assert ventana.hilo() is None
    finally:
        ventana.close()


def test_la_ventana_principal_abre_un_solo_web_reports(app) -> None:
    from app.gui.main_window import MainWindow

    principal = MainWindow()
    try:
        principal._open_web_reports()
        primera = principal._web_reports_window
        principal._open_web_reports()

        assert primera is not None
        assert principal._web_reports_window is primera
        assert principal.btn_web_reports.text() == "Web Reports…"
    finally:
        principal.close()

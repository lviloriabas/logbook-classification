"""Las celdas de la tabla que llevan a Web Search."""

from __future__ import annotations

from PySide6.QtCore import Qt

from app.airvault.config import AirVaultConfig
from app.airvault.web_reports import (
    parsear_filas,
    url_busqueda_del_libro,
    url_busqueda_rango,
)
from app.gui.web_reports_window import (
    COLUMNA_BITACORA,
    COLUMNA_MATRICULA_INDEXADA,
    COLUMNA_RANGO_LIBRO,
    ROL_ENLACE,
    WebReportsWindow,
)


def _fila(matricula: str, detalle: str, rango: str = "2008150 - 2008199"):
    return [
        {"t": matricula},
        {"t": "Copa-7 (50)"},
        {"t": ""},
        {"t": rango},
        {"t": "1/1/2025 - 1/31/2025"},
        {"t": ""},
        {"t": ""},
        {"t": ""},
        {"t": ""},
        {"t": detalle},
    ]


def _excepciones(*filas):
    return parsear_filas(list(filas), AirVaultConfig())


def test_el_rango_abre_el_libro_entero() -> None:
    config = AirVaultConfig(base_url="https://airvault.example", repo_id=77)

    url = url_busqueda_del_libro(config, "2008150 - 2008199")

    assert url == url_busqueda_rango(config, "2008150", "2008199")
    assert "3=2008150%094=2008199" in url


def test_un_rango_que_no_se_lee_no_inventa_enlace() -> None:
    config = AirVaultConfig()

    assert url_busqueda_del_libro(config, "") == ""
    assert url_busqueda_del_libro(config, "sin numeros") == ""


def test_la_excepcion_trae_las_dos_busquedas() -> None:
    excepcion = _excepciones(
        _fila("HP-9913CMP", "DUPLICATED 2008159(3x)")
    )[0]

    assert "3=2008159%094=2008159" in excepcion.url_busqueda
    assert "3=2008150%094=2008199" in excepcion.url_busqueda_libro


def test_la_mal_indexada_ensena_las_dos_matriculas_juntas(
    app, tmp_path
) -> None:
    """La del libro es la que le toca; la de al lado, donde quedó."""
    ventana = WebReportsWindow(tmp_path)
    try:
        ventana._al_recibir(
            _excepciones(
                _fila("HP-9913CMP", "2008152 MIS-INDEX to ACN [HP-9813CMP]")
            )
        )

        assert ventana.tabla.item(0, 1).text() == "HP-9913CMP"
        assert (
            ventana.tabla.item(0, COLUMNA_MATRICULA_INDEXADA).text()
            == "HP-9813CMP"
        )
    finally:
        ventana.close()


def test_la_duplicada_deja_vacia_la_matricula_indexada(
    app, tmp_path
) -> None:
    """El reporte no la dice, y una celda en blanco no afirma nada."""
    ventana = WebReportsWindow(tmp_path)
    try:
        ventana._al_recibir(
            _excepciones(_fila("HP-9913CMP", "DUPLICATED 2008159(3x)"))
        )

        assert ventana.tabla.item(0, 1).text() == "HP-9913CMP"
        assert ventana.tabla.item(0, COLUMNA_MATRICULA_INDEXADA).text() == ""
    finally:
        ventana.close()


def test_las_dos_columnas_quedan_subrayadas_y_con_su_direccion(
    app, tmp_path
) -> None:
    ventana = WebReportsWindow(tmp_path)
    try:
        ventana._al_recibir(
            _excepciones(_fila("HP-9913CMP", "DUPLICATED 2008159(3x)"))
        )

        bitacora = ventana.tabla.item(0, COLUMNA_BITACORA)
        libro = ventana.tabla.item(0, COLUMNA_RANGO_LIBRO)
        assert bitacora.font().underline()
        assert libro.font().underline()
        assert "3=2008159%094=2008159" in bitacora.data(ROL_ENLACE)
        assert "3=2008150%094=2008199" in libro.data(ROL_ENLACE)
        assert "Web Search" in bitacora.toolTip()
        assert "Web Search" in libro.toolTip()
        # Las demas columnas siguen siendo texto y no abren nada.
        assert not ventana.tabla.item(0, 0).font().underline()
        assert not ventana.tabla.item(0, 0).data(ROL_ENLACE)
    finally:
        ventana.close()


def test_sin_rango_legible_la_celda_no_se_marca(app, tmp_path) -> None:
    ventana = WebReportsWindow(tmp_path)
    try:
        ventana._al_recibir(
            _excepciones(
                _fila("HP-9913CMP", "DUPLICATED 2008159(3x)", rango="")
            )
        )

        libro = ventana.tabla.item(0, COLUMNA_RANGO_LIBRO)
        assert not libro.font().underline()
        assert not libro.data(ROL_ENLACE)
        assert ventana.tabla.item(0, COLUMNA_BITACORA).data(ROL_ENLACE)
    finally:
        ventana.close()


def test_el_clic_manda_a_web_search_lo_que_dice_la_celda(
    app, tmp_path, monkeypatch
) -> None:
    ventana = WebReportsWindow(tmp_path)
    pedidos: list[tuple[str, str]] = []
    monkeypatch.setattr(
        WebReportsWindow,
        "_abrir_en_web_search",
        lambda _yo, url, etiqueta: pedidos.append((url, etiqueta)),
    )
    try:
        ventana._al_recibir(
            _excepciones(_fila("HP-9913CMP", "DUPLICATED 2008159(3x)"))
        )

        ventana._al_pulsar_la_celda(0, COLUMNA_BITACORA)
        ventana._al_pulsar_la_celda(0, COLUMNA_RANGO_LIBRO)
        # Una columna sin enlace no lanza nada.
        ventana._al_pulsar_la_celda(0, 0)

        assert [etiqueta for _url, etiqueta in pedidos] == [
            "la bitácora 2008159",
            "el libro 2008150 - 2008199",
        ]
        assert "3=2008159%094=2008159" in pedidos[0][0]
        assert "3=2008150%094=2008199" in pedidos[1][0]
    finally:
        ventana.close()


def test_el_cursor_avisa_solo_sobre_lo_que_abre(app, tmp_path) -> None:
    ventana = WebReportsWindow(tmp_path)
    try:
        ventana._al_recibir(
            _excepciones(_fila("HP-9913CMP", "DUPLICATED 2008159(3x)"))
        )

        ventana._al_pasar_por_la_celda(0, COLUMNA_BITACORA)
        assert (
            ventana.tabla.viewport().cursor().shape()
            == Qt.CursorShape.PointingHandCursor
        )
        ventana._al_pasar_por_la_celda(0, 0)
        assert (
            ventana.tabla.viewport().cursor().shape()
            == Qt.CursorShape.ArrowCursor
        )
    finally:
        ventana.close()


def test_no_abre_nada_mientras_haya_otro_trabajo(app, tmp_path) -> None:
    ventana = WebReportsWindow(tmp_path)
    try:
        ventana._al_recibir(
            _excepciones(_fila("HP-9913CMP", "DUPLICATED 2008159(3x)"))
        )
        ventana.hilo = lambda: WebReportsWindow.hilo
        try:
            ventana._abrir_en_web_search(
                "https://airvault.example", "el libro"
            )
        finally:
            del ventana.hilo

        assert ventana._worker is None
    finally:
        ventana.close()

"""Plan de corrección de las excepciones de Log Page Audit."""

from __future__ import annotations

from pathlib import Path

from app.airvault import correcciones as modulo_correcciones
from app.airvault.config import AirVaultConfig
from app.airvault.correcciones import (
    ACCION_BORRAR,
    ACCION_REINDEXAR,
    ACCION_REVISAR,
    CorrectorLogPageAudit,
    _NavegadorDeCorrecciones,
    copias_en,
    planificar,
    por_antiguedad,
    resumen_del_plan,
)
from app.airvault.web_reports import parsear_filas


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


def test_una_duplicada_conserva_una_copia_y_borra_el_resto() -> None:
    plan = planificar(
        _excepciones(("HP-9913CMP", "DUPLICATED 2008159(3x)"))
    )

    assert [correccion.accion for correccion in plan] == [ACCION_BORRAR]
    assert plan[0].sobran == 2
    assert "2008159" in plan[0].descripcion


def test_una_mal_indexada_sale_del_reporte_sin_adivinar_nada() -> None:
    """Las dos matrículas vienen en la fila: la del libro y la de destino."""
    plan = planificar(
        _excepciones(
            ("HP-9913CMP", "2008152 MIS-INDEX to ACN [HP-9813CMP]")
        )
    )

    assert plan[0].accion == ACCION_REINDEXAR
    assert plan[0].matricula_actual == "HP-9813CMP"
    assert plan[0].matricula_correcta == "HP-9913CMP"


def test_una_duplicada_sin_cuantas_copias_hay_queda_para_revisar() -> None:
    # Sin el «(3x)» el reporte no dice cuántas sobran, y «borrar las demás»
    # sin saber cuántas son es lo que no puede decidir sola la corrección.
    excepciones = _excepciones(("HP-9913CMP", "DUPLICATED 2008159(1x)"))

    plan = planificar(excepciones)

    assert plan[0].accion == ACCION_REVISAR
    assert "cuántas copias" in plan[0].motivo


def test_una_bitacora_repetida_y_mal_indexada_espera_a_la_otra_pasada() -> None:
    """Quitar la copia sobrante puede arreglar de paso el mal indexado."""
    plan = planificar(
        _excepciones(
            ("HP-9913CMP", "DUPLICATED 2008159(2x)"),
            ("HP-9913CMP", "2008159 MIS-INDEX to ACN [HP-9813CMP]"),
        )
    )

    acciones = [correccion.accion for correccion in plan]
    assert acciones == [ACCION_BORRAR, ACCION_REVISAR]
    assert "repetida" in plan[1].motivo


def test_las_duplicadas_se_corrigen_antes_que_las_mal_indexadas() -> None:
    plan = planificar(
        _excepciones(
            ("HP-9913CMP", "2008152 MIS-INDEX to ACN [HP-9813CMP]"),
            ("HP-1523CMP", "DUPLICATED 2284906(2x)"),
        )
    )

    assert [correccion.accion for correccion in plan] == [
        ACCION_BORRAR,
        ACCION_REINDEXAR,
    ]


def test_el_resumen_cuenta_copias_y_paginas_por_separado() -> None:
    plan = planificar(
        _excepciones(
            ("HP-9913CMP", "DUPLICATED 2008159(3x)"),
            ("HP-1523CMP", "DUPLICATED 2284906(2x)"),
            ("HP-9913CMP", "2008152 MIS-INDEX to ACN [HP-9813CMP]"),
            ("HP-9913CMP", "DUPLICATED 2008161(1x)"),
        )
    )

    texto = resumen_del_plan(plan)

    # Tres copias sobrantes (2 + 1) de dos bitácoras, y una reindexada.
    assert "borrar 3 copias sobrantes de 2 bitácoras" in texto
    assert "reindexar 1 página mal indexada" in texto
    assert "Queda 1 caso para revisar" in texto


def test_sin_nada_que_corregir_el_resumen_lo_dice() -> None:
    assert "No hay nada que corregir" in resumen_del_plan([])


# ── lo que se lee de la pantalla de búsqueda ────────────────────────

_REJILLA = [
    ["", "Log Page", "Aircraft", "Scan Date", "Doc Type"],
    ["", "2008159", "HP-9913CMP", "3/14/2025 09:12:00", "Log Page"],
    ["", "2008159", "HP-9913CMP", "1/9/2025 14:05:00", "Log Page"],
    ["", "2008160", "HP-9913CMP", "2/2/2025 08:00:00", "Log Page"],
]


def test_solo_se_toman_las_filas_de_esa_bitacora() -> None:
    copias = copias_en(_REJILLA, "2008159")

    assert len(copias) == 2
    assert {copia.matricula for copia in copias} == {"HP-9913CMP"}


def test_la_columna_de_fecha_se_reconoce_por_su_nombre() -> None:
    """El orden de las columnas lo decide quien monta la búsqueda."""
    copias = copias_en(_REJILLA, "2008159")
    se_queda, sobran = por_antiguedad(copias)

    assert se_queda is not None
    assert se_queda.cuando.month == 1
    assert [copia.cuando.month for copia in sobran] == [3]


def test_sin_fecha_legible_no_se_borra_ninguna_copia() -> None:
    rejilla = [
        ["", "Log Page", "Aircraft"],
        ["", "2008159", "HP-9913CMP"],
        ["", "2008159", "HP-9913CMP"],
    ]

    se_queda, sobran = por_antiguedad(copias_en(rejilla, "2008159"))

    assert se_queda is None
    assert sobran == []


def test_una_sola_copia_no_deja_nada_por_borrar() -> None:
    se_queda, sobran = por_antiguedad(copias_en(_REJILLA, "2008160"))

    assert se_queda is None
    assert sobran == []


def test_cada_copia_se_queda_con_el_identificador_de_su_fila() -> None:
    """Borrar por posición cae en otro documento en cuanto falta una fila.

    AirVault monta la rejilla con jqGrid, que numera cada ``<tr>`` con el
    identificador del registro. Es lo que hay que apuntar: al borrar la
    primera sobrante, las de abajo se corren de sitio.
    """
    rejilla = [
        {"id": "", "celdas": ["", "Log Page", "Aircraft", "Scan Date"]},
        {"id": "884", "celdas": ["", "2008159", "HP-9913CMP", "3/14/2025"]},
        {"id": "231", "celdas": ["", "2008159", "HP-9913CMP", "1/9/2025"]},
        {"id": "907", "celdas": ["", "2008159", "HP-9913CMP", "5/2/2025"]},
    ]

    se_queda, sobran = por_antiguedad(copias_en(rejilla, "2008159"))

    assert se_queda.identificador == "231"
    assert [copia.identificador for copia in sobran] == ["884", "907"]


def test_el_corrector_conserva_las_filas_con_identificador() -> None:
    """El navegador entrega diccionarios, no listas de celdas sueltas."""
    rejilla = [
        {"id": "", "celdas": ["Log Page", "Aircraft", "Scan Date"]},
        {
            "id": "884",
            "celdas": ["2008159", "HP-9913CMP", "3/14/2025"],
        },
    ]

    class _PaginaFalsa:
        def esperar(self, _condicion, _segundos):
            return True

        def evaluar(self, _expresion):
            return rejilla

    leida = CorrectorLogPageAudit._rejilla(_PaginaFalsa())

    assert leida == rejilla
    assert copias_en(leida, "2008159")[0].identificador == "884"


def test_una_rejilla_con_identificadores_llega_hasta_el_borrado() -> None:
    correccion = planificar(
        _excepciones(("HP-9913CMP", "DUPLICATED 2008159(2x)"))
    )[0]
    antes = [
        {"id": "", "celdas": ["Log Page", "Aircraft", "Scan Date"]},
        {
            "id": "vieja",
            "celdas": ["2008159", "HP-9913CMP", "1/9/2025"],
        },
        {
            "id": "nueva",
            "celdas": ["2008159", "HP-9913CMP", "3/14/2025"],
        },
    ]
    despues = antes[:2]

    class _PaginaFalsa:
        lecturas = 0

        def esperar(self, _condicion, _segundos):
            return True

        def evaluar(self, expresion):
            if expresion == modulo_correcciones._LEER_REJILLA:
                self.lecturas += 1
                return antes if self.lecturas == 1 else despues
            return "OK"

    pagina = _PaginaFalsa()
    corrector = CorrectorLogPageAudit(AirVaultConfig())
    rejilla = corrector._rejilla(pagina)

    resultado = corrector._borrar(
        pagina, correccion, copias_en(rejilla, "2008159"), ensayo=False
    )

    assert resultado.hecho
    assert "Borradas 1" in resultado.detalle


def test_una_rejilla_con_identificadores_llega_hasta_el_reindexado() -> None:
    correccion = planificar(
        _excepciones(
            ("HP-9913CMP", "2008152 MIS-INDEX to ACN [HP-9813CMP]")
        )
    )[0]
    rejilla = [
        {"id": "", "celdas": ["Log Page", "Aircraft", "Scan Date"]},
        {
            "id": "pagina",
            "celdas": ["2008152", "HP-9813CMP", "1/9/2025"],
        },
    ]

    class _PaginaFalsa:
        def esperar(self, _condicion, _segundos):
            return True

        def evaluar(self, expresion):
            if expresion == modulo_correcciones._LEER_REJILLA:
                return rejilla
            return "OK"

    pagina = _PaginaFalsa()
    corrector = CorrectorLogPageAudit(AirVaultConfig())
    leida = corrector._rejilla(pagina)

    resultado = corrector._reindexar(
        pagina, correccion, copias_en(leida, "2008152"), ensayo=False
    )

    assert resultado.hecho
    assert "HP-9913CMP" in resultado.detalle


def test_la_correccion_usa_una_pestana_de_fondo_y_deja_edge_abierto(
    monkeypatch, tmp_path: Path
) -> None:
    """Una ventana abierta desde el reporte pertenece a la persona."""
    version = {
        "User-Agent": "Mozilla/5.0 Edg/151",
        "webSocketDebuggerUrl": "ws://127.0.0.1:4321/x",
    }
    pedidos: list[tuple[str, dict]] = []

    class _WebSocketFalso:
        def __init__(self, _url):
            pass

        def pedir(self, metodo, **parametros):
            pedidos.append((metodo, parametros))
            return {"targetId": "temporal"}

        def cerrar(self):
            pass

    class _SesionProhibida:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("no debe abrir otro Edge")

    monkeypatch.setattr(
        modulo_correcciones, "_puerto_anotado", lambda _perfil: 4321
    )
    monkeypatch.setattr(
        modulo_correcciones, "_version_en", lambda _puerto: version
    )
    monkeypatch.setattr(
        modulo_correcciones, "_edges_del_perfil", lambda _perfil: []
    )
    monkeypatch.setattr(modulo_correcciones, "_WebSocket", _WebSocketFalso)
    monkeypatch.setattr(
        modulo_correcciones, "SesionDeNavegador", _SesionProhibida
    )

    with _NavegadorDeCorrecciones(tmp_path) as navegador:
        encontrada = navegador.abrir("https://airvault", espera_s=30.0)
        target_id = navegador.abrir_pestana(
            "https://airvault/busqueda", encontrada
        )

    assert target_id == "temporal"
    assert pedidos == [
        (
            "Target.createTarget",
            {"url": "https://airvault/busqueda", "background": True},
        )
    ]


def test_la_correccion_sin_edge_visible_abre_uno_oculto(
    monkeypatch, tmp_path: Path
) -> None:
    estados: list[object] = []

    class _SesionFalsa:
        def __init__(self, _perfil, visible=True):
            estados.append(("visible", visible))

        def abrir(self, url, espera_s):
            estados.append(("abrir", url, espera_s))
            return {"webSocketDebuggerUrl": "ws://oculto"}

        def abrir_pestana(self, url, version):
            estados.append(("pestaña", url, version))
            return "temporal"

        def cerrar(self):
            estados.append("cerrar")

    monkeypatch.setattr(
        modulo_correcciones, "_puerto_anotado", lambda _perfil: None
    )
    monkeypatch.setattr(
        modulo_correcciones, "_edges_del_perfil", lambda _perfil: []
    )
    monkeypatch.setattr(
        modulo_correcciones, "SesionDeNavegador", _SesionFalsa
    )

    with _NavegadorDeCorrecciones(tmp_path) as navegador:
        version = navegador.abrir("https://airvault", espera_s=30.0)
        navegador.abrir_pestana("https://airvault/busqueda", version)

    assert estados[0] == ("visible", False)
    assert estados[-1] == "cerrar"

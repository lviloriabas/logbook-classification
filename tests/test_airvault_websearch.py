"""La consulta a Web Search: la unica que ve un batch ya completado.

Completar un batch lo saca de la cola de Web Index, asi que desde ese
momento ninguna consulta a la cola lo encuentra. Web Search si lo tiene, y
lo tiene por numero de bitacora, que es la identidad que sobrevive a
reprocesar los escaneos y a borrar la memoria local.

Como AirVault no documenta esa consulta, la ruta se descubre en ejecucion.
Lo que se comprueba aqui es sobre todo lo que impide que ese descubrimiento
mienta: una ruta que no se ha probado con un control positivo no sirve para
afirmar que una bitacora no esta.
"""

from __future__ import annotations

import base64
import json

from app.airvault.config import CAMPO_LOG_NUMBER, AirVaultConfig
from app.airvault.websearch import (
    Buscador,
    candidatas,
    indice_en,
    muestra_de,
    revisar_batch,
)


class SesionFalsa:
    """Contesta como AirVault a una ruta concreta y a ninguna mas."""

    def __init__(self, ruta="", publicadas=(), portada="", scripts=None,
                 indices=None):
        self.ruta = ruta
        self.publicadas = {str(n) for n in publicadas}
        self.portada = portada
        self.scripts = scripts or {}
        # Columnas de mas que la fila trae de esa bitacora, como las
        # devolveria una vista con los indices puestos.
        self.indices = indices or {}
        self.pedidos = []

    def get(self, ruta, params=None, json_esperado=True):
        self.pedidos.append((ruta, dict(params or {})))
        if not json_esperado:
            if ruta == "/zfp/":
                return self.portada
            if ruta in self.scripts:
                return self.scripts[ruta]
            raise RuntimeError(f"404 {ruta}")
        if ruta != self.ruta:
            raise RuntimeError(f"404 {ruta}")
        codificado = str((params or {}).get("encodedValues") or "")
        if not codificado:
            # Solo entiende una forma de preguntar, como el modulo de
            # verdad: las demas plantillas tienen que descartarse solas.
            return {"rows": []}
        texto = base64.b64decode(codificado).decode("utf-8")
        numero = texto.split("=", 1)[1]
        if numero not in self.publicadas:
            return {"rows": []}
        celda = {"C_LogNo": numero, "batchid": "003SRO"}
        celda.update(self.indices.get(numero, {}))
        return {"rows": [{"cell": celda}]}


def _buscador(sesion, controles=("2312238",), **extra):
    return Buscador(
        sesion=sesion, config=AirVaultConfig(), controles=controles, **extra
    )


# ── que la ruta salga de la propia pagina ──────────────────────────

def test_las_candidatas_salen_de_la_portada_y_de_sus_scripts():
    sesion = SesionFalsa(
        portada=(
            '<script src="/zfp/js/busca.js"></script>'
            '<a href="/zfp/Search/GetSearchResults">Buscar</a>'
        ),
        scripts={"/zfp/js/busca.js": 'url: "/zfp/Query/Run"'},
    )

    encontradas = candidatas(sesion)

    # Lo que el modulo declara de si mismo va primero; la lista escrita en
    # el codigo queda de reserva, que es la que envejece.
    assert encontradas[:2] == [
        "/zfp/Search/GetSearchResults", "/zfp/Query/Run"
    ]
    assert "/zfp/Home/Search" in encontradas


def test_no_se_prueban_las_rutas_que_escriben():
    """Guardar o borrar una busqueda no es consultarla."""
    sesion = SesionFalsa(
        portada=json.dumps(
            {
                "guardar": "/zfp/Search/SaveSearch",
                "borrar": "/zfp/Search/DeleteSearch",
                "correr": "/zfp/Search/GetSearchResults",
            }
        )
    )

    encontradas = candidatas(sesion)

    assert "/zfp/Search/SaveSearch" not in encontradas
    assert "/zfp/Search/DeleteSearch" not in encontradas
    assert encontradas[0] == "/zfp/Search/GetSearchResults"


# ── el control positivo ────────────────────────────────────────────

def test_sin_control_positivo_no_se_responde_que_no_esta():
    """Una ruta equivocada tambien contesta que no hay nada.

    Sin una bitacora que se sepa publicada, un «no aparece» no distingue
    «no esta» de «pregunte donde no era», y con esa respuesta se autorizaria
    justo la carga que hay que impedir.
    """
    sesion = SesionFalsa(ruta="/zfp/Search/GetSearchResults")
    buscador = _buscador(sesion, controles=())

    consulta = buscador.publicada("2312240")

    assert consulta.publicada is None
    assert "completada" in consulta.motivo
    assert not sesion.pedidos, "no se pregunta lo que no se puede interpretar"


def test_la_ruta_se_da_por_buena_cuando_encuentra_el_control():
    sesion = SesionFalsa(
        ruta="/zfp/Search/GetSearchResults",
        publicadas=["2312238", "2312240"],
        portada='"/zfp/Search/GetSearchResults"',
    )
    buscador = _buscador(sesion)

    assert buscador.publicada("2312240").publicada is True
    assert buscador.publicada("2312299").publicada is False
    assert buscador.ruta == "/zfp/Search/GetSearchResults"


def test_la_ruta_encontrada_se_guarda_para_no_volver_a_buscarla(tmp_path):
    config = tmp_path / "airvault.json"
    sesion = SesionFalsa(
        ruta="/zfp/Search/GetSearchResults", publicadas=["2312238"]
    )
    buscador = _buscador(sesion, ruta_config=config)

    assert buscador.preparar()

    guardado = json.loads(config.read_text(encoding="utf-8"))
    assert guardado["ruta_websearch"] == "/zfp/Search/GetSearchResults"
    assert guardado["parametros_websearch"] == "encodedValues"


def test_una_ruta_guardada_que_ya_no_sirve_se_vuelve_a_descubrir():
    """Si AirVault cambia, la ruta vieja falla el control y se busca otra."""
    sesion = SesionFalsa(
        ruta="/zfp/Search/GetResults",
        publicadas=["2312238"],
        portada='"/zfp/Search/GetResults"',
    )
    config = AirVaultConfig(
        ruta_websearch="/zfp/Search/Antigua",
        parametros_websearch="encodedValues",
    )
    buscador = Buscador(
        sesion=sesion, config=config, controles=["2312238"]
    )

    assert buscador.preparar()
    assert buscador.ruta == "/zfp/Search/GetResults"


def test_un_numero_dentro_de_otro_no_cuenta_como_encontrado():
    """Siete digitos aparecen por casualidad dentro de un identificador."""

    class SesionConRuido:
        def get(self, ruta, params=None, json_esperado=True):
            return {"rows": [{"cell": {"docId": "99231223800112"}}]}

    buscador = Buscador(
        sesion=SesionConRuido(),
        config=AirVaultConfig(
            ruta_websearch="/zfp/Search/GetSearchResults",
            parametros_websearch="encodedValues",
        ),
        controles=["2312238"],
    )

    # Ni siquiera el control se da por hallado, asi que la ruta no se
    # acepta y nada de lo que conteste se usa para decidir.
    assert not buscador.preparar()


# ── la muestra ─────────────────────────────────────────────────────

def test_la_muestra_se_reparte_a_lo_largo_del_batch():
    """Un batch subido a medias tiene las de arriba y no las de abajo."""
    numeros = [str(2312200 + n) for n in range(20)]

    assert muestra_de(numeros, 3) == ["2312200", "2312210", "2312219"]
    assert muestra_de(["2312200"], 3) == ["2312200"]
    assert muestra_de([], 3) == []


def test_una_bitacora_publicada_basta_para_dar_el_batch_por_subido():
    sesion = SesionFalsa(
        ruta="/zfp/Search/GetSearchResults",
        publicadas=["2312238", "2312250"],
    )
    buscador = _buscador(sesion)

    veredicto = revisar_batch(
        buscador, ["2312240", "2312250", "2312260"], cuantas=3
    )

    assert veredicto.ya_publicado
    assert veredicto.publicadas == ["2312250"]
    assert "2312250" in veredicto.resumen()


def test_un_batch_que_no_esta_publicado_no_bloquea_nada():
    sesion = SesionFalsa(
        ruta="/zfp/Search/GetSearchResults", publicadas=["2312238"]
    )
    buscador = _buscador(sesion)

    veredicto = revisar_batch(buscador, ["2312240", "2312241"], cuantas=2)

    assert not veredicto.ya_publicado
    assert veredicto.concluyente
    assert veredicto.ausentes == ["2312240", "2312241"]


def test_lo_que_no_se_pudo_consultar_no_se_da_por_ausente():
    """Un fallo de red no es un «no esta»: no concluye nada."""

    class SesionCaida:
        def get(self, ruta, params=None, json_esperado=True):
            raise RuntimeError("sin red")

    buscador = Buscador(
        sesion=SesionCaida(),
        config=AirVaultConfig(
            ruta_websearch="/zfp/Search/GetSearchResults",
            parametros_websearch="encodedValues",
        ),
        controles=["2312238"],
    )

    veredicto = revisar_batch(buscador, ["2312240"], cuantas=1)

    assert not veredicto.concluyente
    assert not veredicto.ya_publicado
    assert veredicto.motivo


def test_la_consulta_va_por_el_campo_log_page_number():
    sesion = SesionFalsa(
        ruta="/zfp/Search/GetSearchResults", publicadas=["2312238"]
    )
    _buscador(sesion).publicada("2312238")

    consultas = [
        params for ruta, params in sesion.pedidos
        if ruta == "/zfp/Search/GetSearchResults" and "encodedValues" in params
    ]
    assert consultas
    texto = base64.b64decode(consultas[0]["encodedValues"]).decode("utf-8")
    assert texto == f"{CAMPO_LOG_NUMBER}=2312238"


# -- los indices de una bitacora publicada -------------------------

def test_de_la_fila_salen_la_matricula_y_la_fecha_de_la_bitacora():
    sesion = SesionFalsa(
        ruta="/zfp/Search/GetSearchResults",
        publicadas=["2312238"],
        indices={"2312238": {
            "C_Aircraft": "HP-1376CMP",
            "C_StartDate": "05/01/2025",
            "C_EndDate": "05/14/2025",
        }},
    )

    indice = _buscador(sesion).indice("2312238")

    assert indice.matricula == "HP-1376CMP"
    # Entre las dos fechas de la fila manda la columna de fin, que es la
    # que este programa escribe con la fecha de la bitacora.
    assert indice.fecha == "2025-05-14"


def test_dos_matriculas_en_la_misma_fila_no_son_un_dato():
    sesion = SesionFalsa(
        ruta="/zfp/Search/GetSearchResults",
        publicadas=["2312238"],
        indices={"2312238": {
            "C_Aircraft": "HP-1376CMP",
            "C_Comentario": "antes indexada como HP-1835CMP",
        }},
    )

    indice = _buscador(sesion).indice("2312238")

    assert indice.matricula == ""


def test_dos_fechas_sin_columna_que_las_distinga_no_dan_ninguna():
    datos = {"rows": [{"cell": [
        "2312238", "HP-1376CMP", "05/01/2025", "05/14/2025",
    ]}]}

    indice = indice_en(datos, "2312238")

    assert indice.matricula == "HP-1376CMP"
    assert indice.fecha == ""


def test_no_se_lee_la_fila_de_la_bitacora_de_al_lado():
    datos = {"rows": [
        {"cell": {"C_LogNo": "2312237", "C_Aircraft": "HP-1835CMP"}},
        {"cell": {"C_LogNo": "2312238", "C_Aircraft": "HP-1376CMP"}},
    ]}

    assert indice_en(datos, "2312238").matricula == "HP-1376CMP"


def test_una_bitacora_que_no_esta_no_devuelve_indice():
    assert indice_en({"rows": []}, "2312238") is None


def test_sin_control_conocido_la_propia_bitacora_descubre_la_ruta():
    """Leer indices no necesita un control sabido de antemano.

    Encontrar la bitacora que se busca ya prueba que la ruta mira donde hay
    que mirar: una ruta equivocada tendria que devolver una fila con ese
    numero de siete digitos dentro. Lo que sigue exigiendo un control es
    afirmar que una bitacora no esta publicada.
    """
    sesion = SesionFalsa(
        ruta="/zfp/Search/GetSearchResults",
        publicadas=["2312238"],
        indices={"2312238": {"C_Aircraft": "HP-1376CMP"}},
    )
    buscador = Buscador(sesion=sesion, config=AirVaultConfig(), controles=())

    assert buscador.indice("2312238").matricula == "HP-1376CMP"
    # Y sigue sin poder decir que una bitacora no esta publicada.
    assert buscador.publicada("2312240").publicada is None


def test_la_ruta_descubierta_asi_no_se_vuelve_a_buscar():
    sesion = SesionFalsa(
        ruta="/zfp/Search/GetSearchResults",
        publicadas=["2312238", "2312239"],
        indices={"2312238": {"C_Aircraft": "HP-1376CMP"},
                 "2312239": {"C_Aircraft": "HP-1376CMP"}},
    )
    buscador = Buscador(sesion=sesion, config=AirVaultConfig(), controles=())

    buscador.indice("2312238")
    pedidos = len(sesion.pedidos)
    buscador.indice("2312239")

    assert len(sesion.pedidos) == pedidos + 1

"""Cancelar en AirVault se nota en el acto, no cuando el servidor conteste.

La cancelación era una bandera que el hilo miraba entre paso y paso, y entre
dos pasos podía haber una petición esperando hasta sesenta segundos, tres
intentos con esperas de cinco y diez segundos entre ellos, o la ventana de
acceso esperando cinco minutos. Pulsar Cancelar y esperar tres minutos a que
la ventana reaccionara no se distingue de un cuelgue.
"""

from __future__ import annotations

import threading
import time

import pytest
import requests

from app.airvault.config import AirVaultConfig
from app.airvault.session import (
    ErrorDeConexion,
    SesionAirVault,
    SesionCancelada,
)

from test_airvault_errores import HttpFalso, RespuestaFalsa


def _sesion(respuestas, reintentos=3, espera=0.0) -> SesionAirVault:
    config = AirVaultConfig(reintentos=reintentos, espera_reintento_s=espera)
    sesion = SesionAirVault(config, sesion=HttpFalso(respuestas))
    sesion.usar_cookie("FedAuth=x")
    return sesion


def test_la_espera_entre_reintentos_se_corta_al_cancelar():
    """Diez segundos de espera son diez segundos de ventana muerta."""
    sesion = _sesion([RespuestaFalsa()])
    inicio = time.monotonic()
    threading.Timer(0.05, sesion.cancelar).start()

    with pytest.raises(SesionCancelada):
        sesion.esperar(30.0)

    assert time.monotonic() - inicio < 5.0


def test_sin_cancelar_la_espera_se_cumple():
    sesion = _sesion([RespuestaFalsa()])
    inicio = time.monotonic()
    sesion.esperar(0.2)
    assert time.monotonic() - inicio >= 0.15


def test_cancelada_no_se_reintenta_nada():
    """Insistir contra un servidor del que ya nadie espera respuesta."""
    sesion = _sesion(
        [requests.ConnectionError("se cayo la red")] * 3, espera=5.0
    )
    sesion.cancelar()

    with pytest.raises(SesionCancelada):
        sesion.get("/index/Batch/GetBatches")

    # Ni siquiera se llegó a intentar la primera vez.
    assert sesion.http.pedidas == []


def test_cancelar_a_mitad_de_una_peticion_no_se_lee_como_un_corte_de_red():
    """Cerrar el pool hace fallar lo que estaba en vuelo; no es un fallo."""
    sesion = _sesion([requests.ConnectionError("pool cerrado")] * 3)

    def al_pedir(*_a, **_k):
        # Lo que hace ``cancelar`` desde el hilo de la ventana mientras esta
        # petición está esperando respuesta.
        sesion.cancelar()
        raise requests.ConnectionError("pool cerrado")

    sesion.http.request = al_pedir

    with pytest.raises(SesionCancelada):
        sesion.get("/x")


def test_cancelar_cierra_el_pool_de_conexiones():
    """Es lo único que puede abortar una petición ya en vuelo."""
    sesion = _sesion([RespuestaFalsa()])
    cerrados = []
    sesion.http.close = lambda: cerrados.append(True)

    sesion.cancelar()

    assert cerrados == [True]
    assert sesion.cancelada


def test_reanudar_devuelve_la_sesion_al_trabajo():
    """Soltar los batches tomados son peticiones que existen por cancelar."""
    sesion = _sesion([RespuestaFalsa(json_data={"ok": True})])
    sesion.cancelar()
    sesion.reanudar()

    assert not sesion.cancelada
    assert sesion.get("/x") == {"ok": True}


def test_las_sesiones_paralelas_se_cancelan_juntas():
    """Subir e indexar avanzan por carriles distintos del mismo trabajo."""
    # Con una sesión de verdad: clonar copia el tarro de cookies, y el
    # HTTP falso de las otras pruebas no tiene uno que recorrer.
    sesion = SesionAirVault(AirVaultConfig())
    sesion.usar_cookie("FedAuth=x")
    paralela = sesion.clonar()

    sesion.cancelar()

    assert paralela.cancelada
    with pytest.raises(SesionCancelada):
        paralela.esperar(30.0)


def test_una_sesion_sin_cancelar_espera_y_reintenta_como_siempre():
    """La cancelación no puede cambiar el camino normal."""
    sesion = _sesion([
        requests.ConnectionError("se cayo la red"),
        RespuestaFalsa(json_data={"records": 3}),
    ])
    esperas = []
    sesion.dormir = esperas.append

    assert sesion.get("/index/Batch/GetBatches") == {"records": 3}
    assert len(sesion.http.pedidas) == 2
    assert esperas == [0.0]


def test_agotar_los_reintentos_sigue_siendo_un_error_de_conexion():
    """Un servidor caído no es una cancelación y no se puede confundir."""
    sesion = _sesion([requests.ConnectionError("x")] * 3)
    sesion.dormir = lambda _s: None

    with pytest.raises(ErrorDeConexion):
        sesion.get("/x")

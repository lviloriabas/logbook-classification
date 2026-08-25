"""El arranque de Edge: quien vive, quien muere y quien solo avisa.

Va en un archivo aparte de :mod:`tests.test_airvault_navegador` porque
``test_gui_shutdown`` arrastra una carrera propia de Qt que se despierta
cuando cambia el reparto de pruebas anteriores. Aqui no se lanza Edge: se
sustituye lo que el modulo usa para hablar con el sistema.
"""

from __future__ import annotations

import json
import types
from pathlib import Path

import pytest

from app.airvault import navegador
from app.airvault.navegador import ErrorDeNavegador


class _Respuesta:
    """Lo justo de una respuesta HTTP para que ``json.load`` la lea."""

    def __init__(self, cuerpo: str):
        self._cuerpo = cuerpo.encode()

    def read(self, *_a):
        return self._cuerpo

    def __enter__(self):
        return self

    def __exit__(self, *_e):
        return False


class _LanzadorQueSeVa:
    """``msedge.exe`` tal como se comporta: entrega el encargo y se va."""

    def __init__(self, codigo: int):
        self.returncode = codigo

    def __call__(self, *_a, **_k):
        return self

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode

    def terminate(self):
        pass


def _postizos(monkeypatch, lanzador, respuestas, reloj=None):
    """Sustituye ``subprocess``, ``urllib`` y ``time`` **dentro** del módulo.

    Se cambia el nombre en el espacio de ``navegador`` y no el atributo del
    módulo real: parchear ``time.sleep`` de verdad se lo cambia también a
    los hilos de Qt que dejaron vivos otras pruebas.
    """
    monkeypatch.setattr(navegador, "subprocess", types.SimpleNamespace(
        Popen=lanzador, DEVNULL=-3, TimeoutExpired=TimeoutError,
    ))

    def urlopen(_url, timeout=0):
        # Agotado el guion, el puerto deja de contestar: es lo que ve
        # ``cerrar`` cuando el navegador ya se fue.
        siguiente = next(respuestas, OSError("puerto cerrado"))
        if isinstance(siguiente, Exception):
            raise siguiente
        return _Respuesta(json.dumps(siguiente))

    monkeypatch.setattr(navegador, "urllib", types.SimpleNamespace(
        request=types.SimpleNamespace(urlopen=urlopen),
        error=types.SimpleNamespace(URLError=OSError),
    ))
    monkeypatch.setattr(navegador, "time", types.SimpleNamespace(
        monotonic=reloj or (lambda: 0.0), sleep=lambda _s: None,
    ))

    # El cierre pide ``Browser.close`` por el protocolo. Aqui no hay
    # navegador al otro lado, y dejarlo intentar la conexion de verdad se
    # come dos segundos esperando a un puerto que no existe.
    class _WSPostizo:
        pedidos: list = []

        def __init__(self, _url, timeout=15.0):
            pass

        def pedir(self, metodo, **_p):
            _WSPostizo.pedidos.append(metodo)
            return {}

        def cerrar(self):
            pass

    monkeypatch.setattr(navegador, "_WebSocket", _WSPostizo)
    return _WSPostizo


def test_que_el_lanzador_termine_no_es_que_edge_se_haya_caido(monkeypatch,
                                                              tmp_path):
    """``msedge.exe`` entrega el encargo a otro proceso y se va enseguida.

    Su código de salida (0, o 21 cuando avisó a una instancia que ya corría)
    no dice nada del navegador. Darlo por muerto convertía el arranque en
    una carrera: si el puerto tardaba más que el lanzador, el programa
    declaraba el fallo sobre un Edge que estaba abriendo.
    """
    reloj = iter([i * 0.5 for i in range(200)])
    ws = _postizos(
        monkeypatch, _LanzadorQueSeVa(21),
        iter([OSError("todavía no"), OSError("todavía no"),
              {"Browser": "Edg/151", "webSocketDebuggerUrl": "ws://x/y"}]),
        reloj=lambda: next(reloj),
    )
    sesion = navegador.SesionDeNavegador(tmp_path, edge=Path("msedge.exe"))
    try:
        assert sesion.abrir("about:blank")["Browser"] == "Edg/151"
    finally:
        sesion.cerrar()
    # Y al cerrar se le pide por las buenas, no se le mata.
    assert "Browser.close" in ws.pedidos


def test_si_nadie_contesta_tras_irse_el_lanzador_si_es_un_fallo(monkeypatch,
                                                               tmp_path):
    """Lo que sí vale: el lanzador se fue y el puerto sigue mudo un rato."""
    reloj = iter([i * 1.0 for i in range(200)])
    _postizos(
        monkeypatch, _LanzadorQueSeVa(0),
        iter(OSError("puerto mudo") for _ in range(200)),
        reloj=lambda: next(reloj),
    )
    sesion = navegador.SesionDeNavegador(tmp_path, edge=Path("msedge.exe"))
    try:
        with pytest.raises(ErrorDeNavegador) as fallo:
            sesion.abrir("about:blank", espera_s=120.0)
        # Y no se gasta la espera entera: se rinde al pasar la gracia. Se
        # mira aqui, antes de cerrar, porque el cierre tambien lee el reloj.
        assert next(reloj) < 120.0
    finally:
        sesion.cerrar()
    assert "no llego a arrancar" in str(fallo.value)

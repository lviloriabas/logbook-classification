"""Cobertura de las rutas propias del ejecutable de AirVault."""

from types import SimpleNamespace

import run_airvault
from app.airvault.config import AirVaultConfig


class TrabajoFalso:
    ultimo = None

    def __init__(self, config, carpeta, manifiesto):
        self.config = config
        self.carpeta = carpeta
        self.manifiesto = manifiesto
        self.subidas = []
        TrabajoFalso.ultimo = self

    def subir(self, sesion, archivo, cliente=None):
        self.subidas.append((sesion, archivo, cliente))


def argumentos(pdf):
    return SimpleNamespace(job="prueba", pdf=pdf)


def test_subir_cli_usa_el_flujo_vigente(monkeypatch, tmp_path):
    manifiesto = object()
    sesion = object()
    monkeypatch.setattr(run_airvault, "CARPETA_TRABAJOS", tmp_path)
    monkeypatch.setattr(run_airvault.manifiestos, "cargar", lambda _p: manifiesto)
    monkeypatch.setattr(run_airvault, "abrir_sesion", lambda _c, _a: sesion)
    monkeypatch.setattr(run_airvault, "ClienteHttp", lambda _s, _c: "cliente")
    monkeypatch.setattr(run_airvault, "Trabajo", TrabajoFalso)

    assert run_airvault.etapa_subir(
        argumentos("una.pdf"), AirVaultConfig()
    ) == 0
    assert TrabajoFalso.ultimo.subidas == [
        (sesion, run_airvault.Path("una.pdf"), "cliente")
    ]

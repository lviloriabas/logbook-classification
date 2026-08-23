"""Preferencias portables de AirVault."""

from __future__ import annotations

import json

from app.airvault.config import AirVaultConfig, guardar_paginas_por_batch


def test_la_cantidad_no_tiene_un_default_fijo_en_el_codigo():
    assert AirVaultConfig().paginas_por_batch is None
    assert AirVaultConfig().espera_reenvio_s == 30 * 60


def test_guarda_la_ultima_cantidad_sin_perder_la_configuracion(tmp_path):
    ruta = tmp_path / "airvault.json"
    ruta.write_text(
        json.dumps({"repo_id": 3209, "paginas_por_batch": 200}),
        encoding="utf-8",
    )

    assert guardar_paginas_por_batch(ruta, 425)

    datos = json.loads(ruta.read_text(encoding="utf-8"))
    assert datos["paginas_por_batch"] == 425
    assert datos["repo_id"] == 3209
    assert AirVaultConfig.load(ruta).paginas_por_batch == 425

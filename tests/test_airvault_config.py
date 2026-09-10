"""Preferencias portables de AirVault."""

from __future__ import annotations

import json

from app.airvault.config import AirVaultConfig, guardar_paginas_por_batch


def test_la_cantidad_no_tiene_un_default_fijo_en_el_codigo():
    assert AirVaultConfig().paginas_por_batch is None
    assert AirVaultConfig().espera_reenvio_s == 30 * 60


def test_usa_el_ejemplo_si_no_hay_preferencias_locales(tmp_path):
    ruta = tmp_path / "airvault.json"
    (tmp_path / "airvault.example.json").write_text(
        json.dumps({"repo_id": 77, "paginas_por_batch": 325}),
        encoding="utf-8",
    )

    config = AirVaultConfig.load(ruta)

    assert config.repo_id == 77
    assert config.paginas_por_batch == 325


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


def test_otras_preferencias_no_ocultan_la_cantidad_del_ejemplo(tmp_path):
    ruta = tmp_path / "airvault.json"
    ejemplo = tmp_path / "airvault.example.json"
    ejemplo.write_text(json.dumps({"paginas_por_batch": 375,
                                   "csv_date_mode": "month_end"}), encoding="utf-8")
    ruta.write_text(json.dumps({"auto_subir": False, "csv_date_mode": "specific"}), encoding="utf-8")
    config = AirVaultConfig.load(ruta)
    assert config.paginas_por_batch == 375
    assert config.auto_subir is False
    assert config.csv_date_mode == "specific"
    assert guardar_paginas_por_batch(ruta, 525)
    assert AirVaultConfig.load(ruta).paginas_por_batch == 525
    assert json.loads(ruta.read_text(encoding="utf-8"))["csv_date_mode"] == "specific"
